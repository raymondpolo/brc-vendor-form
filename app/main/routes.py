# app/main/routes.py
import os
import csv
import io
import re
import base64
import mimetypes
import json
import uuid
from flask import current_app # Import current_app for logging
from markupsafe import Markup
from functools import wraps
from collections import Counter
from datetime import datetime, time, timedelta

from flask import (render_template, request, redirect, url_for, flash,
                   abort, send_from_directory, jsonify, current_app, Response)
from flask_login import login_required, current_user
from sqlalchemy import or_, func, case
import bleach
from pywebpush import webpush, WebPushException

from app import db, csrf # Make sure csrf is imported
from app.main import main
from app.models import (User, WorkOrder, Property, Note, Notification,
                        AuditLog, Attachment, Vendor, Quote, RequestType, PushSubscription)
from app.forms import (NoteForm, ChangeStatusForm, AttachmentForm, NewRequestForm,
                       UpdateAccountForm, ChangePasswordForm, AssignVendorForm, ReportForm, QuoteForm, DeleteRestoreRequestForm, TagForm, ReassignRequestForm, SendFollowUpForm, MarkAsCompletedForm)
from app.email import send_notification_email
from werkzeug.utils import secure_filename
from app.decorators import admin_required, role_required
from app.events import broadcast_new_note
from app.utils import get_denver_now, convert_to_denver, DENVER_TZ, make_denver_aware_start_of_day, make_denver_aware_end_of_day # Import helpers


def get_requester_initials(name):
    parts = name.split()
    if len(parts) > 1:
        return (parts[0][0] + parts[-1][0]).upper()
    elif parts:
        return parts[0][:2].upper()
    return ""

def save_attachment(file, work_order_id, file_type='Attachment'):
    if not file or not file.filename:
        return None
    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    unique_filename = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename))

    attachment = Attachment(filename=unique_filename, user_id=current_user.id, work_order_id=work_order_id, file_type=file_type)
    db.session.add(attachment)
    db.session.commit()
    return attachment

def work_order_to_dict(req):
    """Helper function to convert a WorkOrder object to a dictionary."""
    # Convert date_created to Denver time before formatting if needed
    denver_created_date = convert_to_denver(req.date_created)
    return {
        'id': req.id,
        'date_created': denver_created_date.strftime('%m/%d/%Y') if denver_created_date else '', # Changed format
        'wo_number': req.wo_number,
        'requester_name': req.requester_name,
        'property': req.property,
        'unit': req.unit,
        'address': req.address,
        'property_manager': req.property_manager,
        'status': req.status,
        'request_type': req.request_type_relation.name,
        'tag': req.tag,
        'vendor_name': req.vendor.company_name if req.vendor else 'N/A'
    }

def send_push_notification(user_id, title, body, link):
    # Use Flask logger instead of print
    current_app.logger.info(f"DEBUG PUSH: Entered send_push_notification function for user_id: {user_id}")
    app = current_app._get_current_object()
    with app.app_context():
        user = User.query.get(user_id)
        if not user:
            current_app.logger.warning(f"DEBUG PUSH: User with id {user_id} not found. Exiting function.")
            return

        subscriptions = PushSubscription.query.filter_by(user_id=user.id).all()
        if not subscriptions:
            current_app.logger.info(f"DEBUG PUSH: No push subscriptions found for user {user.name}. Exiting function.")
            return

        current_app.logger.info(f"DEBUG PUSH: Found {len(subscriptions)} subscriptions for user {user.name}.")

        vapid_private_key = current_app.config.get('VAPID_PRIVATE_KEY')
        vapid_claims = {"sub": f"mailto:{current_app.config.get('VAPID_CLAIM_EMAIL', '')}"}

        if not vapid_private_key:
            current_app.logger.error('DEBUG PUSH: VAPID_PRIVATE_KEY is not configured. Cannot send push notifications.')
            return

        for sub in subscriptions:
            try:
                try:
                    sub_json = json.loads(sub.subscription_json)
                except Exception as parse_ex:
                    current_app.logger.error(f"DEBUG PUSH: Could not parse subscription JSON for PushSubscription id={sub.id}: {parse_ex}")
                    current_app.logger.debug(f"DEBUG PUSH: Raw subscription_json: {sub.subscription_json}")
                    continue

                endpoint = sub_json.get('endpoint', '')[:80]
                current_app.logger.info(f"DEBUG PUSH: Sending to subscription endpoint: {endpoint} (subscription id={sub.id})")

                webpush(
                    subscription_info=sub_json,
                    data=json.dumps({'title': title, 'body': body, 'link': link}),
                    vapid_private_key=vapid_private_key,
                    vapid_claims=vapid_claims
                )

                current_app.logger.info("DEBUG PUSH: Successfully sent push notification.")
            except WebPushException as ex:
                current_app.logger.error(f"DEBUG PUSH: Web push failed with exception: {ex}")
                # Log more details if available
                if hasattr(ex, 'response'):
                    current_app.logger.error(f"DEBUG PUSH: WebPushException response: {getattr(ex, 'response', None)}")
            except Exception as e:
                current_app.logger.error(f"DEBUG PUSH: An unexpected error occurred in webpush: {e}", exc_info=True)


# Serve the root-level service worker so browsers can fetch it at '/service-worker.js'
# Some hosting setups don't serve files from the repository root as static files, so
# provide a Flask route to return the file from the project root directory.
@main.route('/service-worker.js')
def service_worker_root():
    try:
        # Project root is parent of the app package root
        project_root = os.path.abspath(os.path.join(current_app.root_path, '..'))
        sw_path = os.path.join(project_root, 'service-worker.js')
        if not os.path.exists(sw_path):
            current_app.logger.error(f"Service worker requested but not found at {sw_path}")
            abort(404)
        return send_from_directory(os.path.dirname(sw_path), os.path.basename(sw_path), mimetype='application/javascript')
    except Exception as e:
        current_app.logger.error(f"Error serving service-worker.js: {e}", exc_info=True)
        abort(500)


@main.route('/')
@login_required
def index():
    if current_user.role in ['Requester', 'Property Manager']:
        return redirect(url_for('main.my_requests'))
    else:
        return redirect(url_for('main.dashboard'))

@main.route('/dashboard')
@login_required
def dashboard():
    if current_user.role in ['Requester', 'Property Manager']:
        return redirect(url_for('main.my_requests'))

    all_statuses = [
        'New', 'Open', 'Pending', 'Quote Requested', 'Quote Sent',
        'Scheduled', 'Closed', 'Cancelled'
    ]

    base_query = WorkOrder.query.filter_by(is_deleted=False)

    status_counts_query = base_query.with_entities(WorkOrder.status, func.count(WorkOrder.id)).group_by(WorkOrder.status).all()
    db_counts = {status: count for status, count in status_counts_query}

    stats = {status: db_counts.get(status, 0) for status in all_statuses}
    stats['totalRequests'] = sum(db_counts.values())

    all_work_orders = base_query.all()
    all_tags = []
    for wo in all_work_orders:
        if wo.tag:
            all_tags.extend(filter(None, wo.tag.split(',')))
    tag_counts = Counter(all_tags)

    tag_stats = {
        "approved": tag_counts.get('Approved', 0),
        "declined": tag_counts.get('Declined', 0),
        "follow_up": tag_counts.get('Follow-up needed', 0),
        "go_back": tag_counts.get('Go-back', 0)
    }

    status_counts_for_chart = Counter(req.status for req in all_work_orders)
    type_counts = Counter(req.request_type_relation.name for req in all_work_orders)
    property_counts = Counter(req.property for req in all_work_orders)
    vendor_counts = Counter(req.vendor.company_name for req in all_work_orders if req.vendor)

    approved_by_pm = Counter(wo.property_manager for wo in all_work_orders if wo.tag and 'Approved' in wo.tag.split(','))
    declined_by_pm = Counter(wo.property_manager for wo in all_work_orders if wo.tag and 'Declined' in wo.tag.split(','))

    goback_work_orders = base_query.filter(WorkOrder.tag.like('%Go-back%')).all()
    goback_by_vendor = Counter(wo.vendor.company_name if wo.vendor else 'Unassigned' for wo in goback_work_orders)

    status_colors = {
        'New': {'bg': 'bg-blue-100', 'text': 'text-blue-800', 'border': 'border-blue-500', 'rgba': 'rgba(59, 130, 246, 0.8)'},
        'Open': {'bg': 'bg-cyan-100', 'text': 'text-cyan-800', 'border': 'border-cyan-500', 'rgba': 'rgba(6, 182, 212, 0.8)'},
        'Pending': {'bg': 'bg-yellow-100', 'text': 'text-yellow-800', 'border': 'border-yellow-400', 'rgba': 'rgba(245, 158, 11, 0.8)'},
        'Quote Requested': {'bg': 'bg-orange-100', 'text': 'text-orange-800', 'border': 'border-orange-500', 'rgba': 'rgba(249, 115, 22, 0.8)'},
        'Quote Sent': {'bg': 'bg-pink-100', 'text': 'text-pink-800', 'border': 'border-pink-500', 'rgba': 'rgba(236, 72, 153, 0.8)'},
        'Scheduled': {'bg': 'bg-purple-100', 'text': 'text-purple-800', 'border': 'border-purple-500', 'rgba': 'rgba(168, 85, 247, 0.8)'},
        'Closed': {'bg': 'bg-gray-100', 'text': 'text-gray-800', 'border': 'border-gray-700', 'rgba': 'rgba(55, 65, 81, 0.8)'},
        'Completed': {'bg': 'bg-green-100', 'text': 'text-green-800', 'border': 'border-green-500', 'rgba': 'rgba(34, 197, 94, 0.8)'},
        'Cancelled': {'bg': 'bg-gray-100', 'text': 'text-gray-800', 'border': 'border-gray-400', 'rgba': 'rgba(156, 163, 175, 0.8)'},
    }

    tag_colors = {
        'Approved': {'bg': 'bg-green-100', 'text': 'text-green-800', 'border': 'border-green-500', 'rgba': 'rgba(34, 197, 94, 0.8)'},
        'Declined': {'bg': 'bg-red-100', 'text': 'text-red-800', 'border': 'border-red-500', 'rgba': 'rgba(239, 68, 68, 0.8)'},
        'Follow-up needed': {'bg': 'bg-purple-100', 'text': 'text-purple-800', 'border': 'border-purple-500', 'rgba': 'rgba(168, 85, 247, 0.8)'},
        'Go-back': {'bg': 'bg-blue-100', 'text': 'text-blue-800', 'border': 'border-blue-500', 'rgba': 'rgba(59, 130, 246, 0.8)'},
    }

    generic_chart_colors = [
        'rgba(54, 162, 235, 0.8)', 'rgba(255, 206, 86, 0.8)',
        'rgba(75, 192, 192, 0.8)', 'rgba(153, 102, 255, 0.8)',
        'rgba(255, 99, 132, 0.8)', 'rgba(255, 159, 64, 0.8)',
        'rgba(128, 128, 128, 0.8)', 'rgba(0, 102, 204, 0.8)',
        'rgba(204, 0, 102, 0.8)', 'rgba(102, 204, 0, 0.8)'
    ]

    chart_data = {
        "status": {
            "labels": list(status_counts_for_chart.keys()),
            "data": list(status_counts_for_chart.values()),
            "colors": [status_colors.get(status, {}).get('rgba', 'rgba(156, 163, 175, 0.8)') for status in status_counts_for_chart.keys()]
        },
        "type": {
            "labels": list(type_counts.keys()),
            "data": list(type_counts.values()),
            "colors": generic_chart_colors
        },
        "property": {
            "labels": list(property_counts.keys()),
            "data": list(property_counts.values()),
            "colors": generic_chart_colors
        },
        "vendor": {
            "labels": list(vendor_counts.keys()),
            "data": list(vendor_counts.values()),
            "colors": generic_chart_colors
        },
        "approved_by_pm": {
            "labels": list(approved_by_pm.keys()),
            "data": list(approved_by_pm.values()),
            "colors": [tag_colors['Approved']['rgba']] * len(approved_by_pm)
        },
        "declined_by_pm": {
            "labels": list(declined_by_pm.keys()),
            "data": list(declined_by_pm.values()),
            "colors": [tag_colors['Declined']['rgba']] * len(declined_by_pm)
        },
        "goback_by_vendor": {
            "labels": list(goback_by_vendor.keys()),
            "data": list(goback_by_vendor.values()),
            "colors": generic_chart_colors
        }
    }

    return render_template(
        'dashboard.html', title='Dashboard', stats=stats, all_statuses=all_statuses,
        tag_stats=tag_stats, chart_data=chart_data, status_colors=status_colors, tag_colors=tag_colors
    )

@main.route('/requests')
@login_required
@admin_required
def all_requests():
    requests_data = WorkOrder.query.filter_by(is_deleted=False).order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in requests_data]
    return render_template('all_requests.html', title='All Requests',
                           requests_json=json.dumps(requests_list))

@main.route('/my-requests')
@login_required
def my_requests():
    query = WorkOrder.query.filter_by(is_deleted=False)
    if current_user.role == 'Property Manager':
        query = query.filter(WorkOrder.property_manager == current_user.name)
    else:
        query = query.filter_by(author=current_user)

    user_requests = query.order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in user_requests]
    return render_template('my_requests.html', title='My Requests',
                           requests_json=json.dumps(requests_list))

@main.route('/shared-with-me')
@login_required
def shared_requests():
    query = WorkOrder.query.filter_by(is_deleted=False).filter(WorkOrder.viewers.contains(current_user))
    requests_data = query.order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in requests_data]
    return render_template('shared_requests.html', title='Shared With Me',
                           requests_json=json.dumps(requests_list))

@main.route('/requests/status/<status>')
@login_required
@admin_required
def requests_by_status(status):
    filtered_requests = WorkOrder.query.filter_by(is_deleted=False, status=status).order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in filtered_requests]
    return render_template('requests_by_status.html', title=f'Requests: {status}',
                           requests_json=json.dumps(requests_list), status=status)

@main.route('/requests/tag/<tag_name>')
@login_required
@admin_required
def requests_by_tag(tag_name):
    tagged_requests = WorkOrder.query.filter_by(is_deleted=False).filter(WorkOrder.tag.like(f'%{tag_name}%')).order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in tagged_requests]
    return render_template('requests_by_tag.html', title=f'Requests Tagged: {tag_name}',
                           requests_json=json.dumps(requests_list), tag_name=tag_name)

@main.route('/request/<int:request_id>', methods=['GET'])
@login_required
def view_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)

    if work_order.is_deleted and current_user.role != 'Super User':
        abort(404)
    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']
    if not (is_author or is_viewer or is_property_manager or is_admin_staff):
        abort(403)

    if not work_order.is_deleted and is_admin_staff and work_order.status == 'New':
        work_order.status = 'Open'
        db.session.add(AuditLog(text='Status changed to Open.', user_id=current_user.id, work_order_id=work_order.id))
        flash('Request status has been updated to Open.', 'info')
        needs_commit = db.session.new or db.session.dirty
        if needs_commit:
             try:
                 db.session.commit()
             except Exception as e:
                 db.session.rollback()
                 current_app.logger.error(f"Error committing status change: {e}")
                 flash('Error updating status.', 'danger')

    db.session.add(AuditLog(text='Viewed the request.', user_id=current_user.id, work_order_id=work_order.id))
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error committing view log: {e}")


    notes = Note.query.filter_by(work_order_id=request_id).order_by(Note.date_posted.asc()).all()
    audit_logs = AuditLog.query.filter_by(work_order_id=request_id).order_by(AuditLog.timestamp.desc()).all()
    note_form = NoteForm()
    status_form = ChangeStatusForm()
    attachment_form = AttachmentForm()
    assign_vendor_form = AssignVendorForm()
    quote_form = QuoteForm()
    delete_form = DeleteRestoreRequestForm()
    tag_form = TagForm()
    reassign_form = ReassignRequestForm()
    follow_up_form = SendFollowUpForm()
    completed_form = MarkAsCompletedForm()
    requester_initials = get_requester_initials(work_order.requester_name)
    quotes = work_order.quotes
    all_users = User.query.filter_by(is_active=True).all()

    return render_template('view_request.html', title=f'Request #{work_order.id}', work_order=work_order, notes=notes,
                           note_form=note_form, status_form=status_form, audit_logs=audit_logs,
                           attachment_form=attachment_form, assign_vendor_form=assign_vendor_form,
                           requester_initials=requester_initials, quote_form=quote_form, quotes=quotes,
                           delete_form=delete_form, tag_form=tag_form, reassign_form=reassign_form,
                           follow_up_form=follow_up_form, all_users=all_users, completed_form=completed_form)


# +++ RESTORED FULL LOGIC to post_note route +++
@main.route('/request/<int:request_id>/post_note', methods=['POST'])
@login_required
# @csrf.exempt # Removed exemption, as JS is sending the token
def post_note(request_id):
    current_app.logger.info(f"--- !!! ENTERED post_note route for request {request_id} !!! ---")

    current_app.logger.info(f"DEBUG NOTE: User ID: {current_user.id}, User Name: {current_user.name}")
    work_order = WorkOrder.query.get_or_404(request_id)
    current_app.logger.info(f"DEBUG NOTE: Fetched WorkOrder ID: {work_order.id}")

    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']
    if not (is_author or is_viewer or is_admin_staff or is_property_manager):
         current_app.logger.warning(f"DEBUG NOTE: Permission denied for user {current_user.id} ({current_user.name}) on request {request_id}")
         return jsonify({'success': False, 'message': 'Permission denied.'}), 403
    current_app.logger.info("DEBUG NOTE: Permission check passed.")

    note_form = NoteForm()
    current_app.logger.info("DEBUG NOTE: NoteForm instantiated.")
    current_app.logger.info(f"DEBUG NOTE: Raw request form data: {request.form}")

    validation_result = note_form.validate_on_submit()
    current_app.logger.info(f"DEBUG NOTE: note_form.validate_on_submit() returned: {validation_result}")

    if validation_result:
        current_app.logger.info("DEBUG NOTE: Note form validated successfully. Entering try block.")
        try:
            note_text = note_form.text.data
            current_app.logger.info(f"DEBUG NOTE: Note text extracted: '{note_text}'")
            # Note timestamp defaults to Denver time
            note = Note(text=note_text, author=current_user, work_order=work_order)
            db.session.add(note)
            current_app.logger.info("DEBUG NOTE: Note object created and added to session.")

            notified_users = set()
            if work_order.author and work_order.author != current_user:
                 notified_users.add(work_order.author)
                 current_app.logger.info(f"DEBUG NOTE: Added author {work_order.author.name} to notified_users.")

            tagged_names = re.findall(r'@(\w+(?:\s\w+)?)', note_text)
            current_app.logger.info(f"DEBUG NOTE: Found mentions: {tagged_names}")
            for name in tagged_names:
                search_name = name.strip()
                tagged_user = User.query.filter(func.lower(User.name) == func.lower(search_name)).first()
                if tagged_user:
                    current_app.logger.info(f"DEBUG NOTE: Found tagged user: {tagged_user.name} (ID: {tagged_user.id})")
                    if tagged_user not in work_order.viewers:
                        work_order.viewers.append(tagged_user)
                        current_app.logger.info(f"DEBUG NOTE: Added {tagged_user.name} to work_order viewers.")
                    if tagged_user != current_user:
                        notified_users.add(tagged_user)
                        current_app.logger.info(f"DEBUG NOTE: Added {tagged_user.name} to notified_users.")
                    else:
                        current_app.logger.info(f"DEBUG NOTE: Tagged user {tagged_user.name} is the current user, not adding to notify list.")
                else:
                    current_app.logger.warning(f"DEBUG NOTE: Could not find user for mention: @{search_name}")

            current_app.logger.info("DEBUG NOTE: Committing note and viewer changes...")
            db.session.commit() # Commit note and viewer changes first
            current_app.logger.info("DEBUG NOTE: Commit successful.")

            current_app.logger.info("DEBUG NOTE: Broadcasting note via Socket.IO...")
            broadcast_new_note(work_order.id, note) # Notify via Socket.IO
            current_app.logger.info("DEBUG NOTE: Broadcast complete.")

            current_app.logger.info(f"DEBUG NOTE: Users to notify via Push/Email: {[user.name for user in notified_users]}")
            if not notified_users:
                 current_app.logger.info("DEBUG NOTE: No users found in notified_users set.")

            for user in notified_users:
                current_app.logger.info(f"DEBUG NOTE: Processing PUSH/EMAIL for user: {user.name} (ID: {user.id})")
                notification_text = f'{current_user.name} mentioned you in a note on Request #{work_order.id}'
                # Notification timestamp defaults to Denver time
                notification = Notification(
                    text=notification_text,
                    link=url_for('main.view_request', request_id=work_order.id),
                    user_id=user.id
                )
                db.session.add(notification)
                current_app.logger.info(f"DEBUG NOTE: Added Notification object for user {user.id} to session.")

                current_app.logger.info(f"DEBUG PUSH (Pre-call): Preparing to send push for user {user.id} ({user.name})")
                push_link = url_for('main.view_request', request_id=work_order.id, _external=True)
                current_app.logger.info(f"DEBUG PUSH (Pre-call): Link generated: {push_link}")

                send_push_notification(
                    user.id,
                    'New Mention',
                    notification_text,
                    push_link
                )
                current_app.logger.info(f"DEBUG PUSH (Post-call): Returned from send_push_notification for user {user.id}")

                current_app.logger.info(f"DEBUG EMAIL (Pre-call): Preparing email for user {user.id} ({user.name})")
                email_body = f"""
                <p><b>{current_user.name}</b> mentioned you in a note on Request #{work_order.id} for property <b>{work_order.property}</b>.</p>
                <p><b>Note:</b></p>
                <p style="padding-left: 20px; border-left: 3px solid #eee;">{note.text}</p>
                """
                send_notification_email(
                    subject=f"New Note on Request #{work_order.id}",
                    recipients=[user.email],
                    text_body=notification_text,
                    html_body=render_template(
                        'email/notification_email.html',
                        title="New Note on Request",
                        user=user,
                        body_content=email_body,
                        link=url_for('main.view_request', request_id=work_order.id, _external=True)
                    )
                )
                current_app.logger.info(f"DEBUG EMAIL (Post-call): Returned from send_notification_email for user {user.id}")

            current_app.logger.info("DEBUG NOTE: Committing notifications...")
            db.session.commit() # Commit notifications
            current_app.logger.info("DEBUG NOTE: Notifications commit successful. Returning success JSON.")
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error posting note: {e}", exc_info=True)
            return jsonify({'success': False, 'message': 'An internal error occurred.'}), 500
    else:
        current_app.logger.warning(f"DEBUG NOTE: Note form validation FAILED. Errors: {note_form.errors}")
        return jsonify({'success': False, 'errors': note_form.errors}), 400
# --- END ROUTE ---


# --- Keep ALL other existing routes ---
@main.route('/request/<int:request_id>/mark_as_completed', methods=['POST'])
@login_required
def mark_as_completed(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = MarkAsCompletedForm()
    if form.validate_on_submit():
        work_order.status = 'Completed'
        work_order.date_completed = get_denver_now() # <-- Use Denver time
        db.session.add(AuditLog(text='Request marked as completed.', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash('Request has been marked as completed.', 'success')
    else:
        flash('There was an error marking the request as completed.', 'danger')
    return redirect(url_for('main.view_request', request_id=request_id))


@main.route('/request/<int:request_id>/send_follow_up', methods=['POST'])
@login_required
@admin_required
def send_follow_up(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = SendFollowUpForm()
    if form.validate_on_submit():
        recipient = form.recipient.data
        cc = form.cc.data
        subject = form.subject.data
        body = form.body.data

        recipients = [recipient]
        cc_list = [email.strip() for email in cc.split(',')] if cc else []

        html_body = f"<p>{body.replace(chr(10), '<br>')}</p>"

        send_notification_email(
            subject=subject,
            recipients=recipients,
            cc=cc_list,
            html_body=html_body,
            text_body=body
        )
        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text=f"Follow-up email sent to {recipient}", user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()

        flash('Follow-up email sent successfully!', 'success')
    else:
        flash('Failed to send follow-up email. Please check the form.', 'danger')

    return redirect(url_for('main.view_request', request_id=request_id))


@main.route('/request/<int:request_id>/delete', methods=['POST'])
@login_required
@role_required(['Super User'])
def delete_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = DeleteRestoreRequestForm()
    if form.validate_on_submit():
        work_order.is_deleted = True
        work_order.deleted_at = get_denver_now() # <-- Use Denver time
        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text='Request soft-deleted.', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash(f'Request #{work_order.id} has been deleted. It can be restored.', 'success')
        return redirect(url_for('main.dashboard'))
    else:
        flash('Invalid request to delete the work order.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))


@main.route('/request/<int:request_id>/restore', methods=['POST'])
@login_required
@role_required(['Super User'])
def restore_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = DeleteRestoreRequestForm()
    if form.validate_on_submit():
        work_order.is_deleted = False
        work_order.deleted_at = None
        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text='Request restored.', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash(f'Request #{work_order.id} has been restored.', 'success')
        return redirect(url_for('main.view_request', request_id=request_id))
    else:
        flash('Invalid request to restore the work order.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/request/<int:request_id>/permanently-delete', methods=['POST'])
@login_required
@role_required(['Super User'])
def permanently_delete_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = DeleteRestoreRequestForm()
    if form.validate_on_submit():
        # Related items (notes, logs, attachments, etc.) should cascade delete based on model setup
        db.session.delete(work_order)
        db.session.commit()
        flash(f'Request #{work_order.id} has been permanently deleted.', 'success')
        return redirect(url_for('main.deleted_requests'))
    else:
        flash('Invalid request to permanently delete the work order.', 'danger')
        return redirect(url_for('main.deleted_requests'))

@main.route('/deleted-requests')
@login_required
@role_required(['Super User'])
def deleted_requests():
    deleted = WorkOrder.query.filter_by(is_deleted=True).order_by(WorkOrder.deleted_at.desc()).all()
    form = DeleteRestoreRequestForm()
    return render_template('deleted_requests.html', title='Deleted Requests', requests=deleted, form=form)


@main.route('/change_status/<int:request_id>', methods=['POST'])
@login_required
@admin_required
def change_status(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = ChangeStatusForm()
    form.status.choices = [c for c in form.status.choices if c[0] != 'New']
    if current_user.role in ['Admin', 'Scheduler', 'Super User']:
        form.status.choices = [c for c in form.status.choices if c[0] not in ['Approved', 'Quote Declined']]

    if form.validate_on_submit():
        new_status = form.status.data

        if new_status == 'Scheduled':
            if not work_order.vendor_id:
                flash('A vendor must be assigned before scheduling.', 'danger')
                return redirect(url_for('main.view_request', request_id=request_id))
            if not form.scheduled_date.data:
                flash('A scheduled date is required to change the status to "Scheduled".', 'danger')
                return redirect(url_for('main.view_request', request_id=request_id))

        old_status = work_order.status
        if old_status == new_status:
            # Still update scheduled date if provided and status is Scheduled
            if new_status == 'Scheduled' and form.scheduled_date.data:
                try:
                    new_scheduled_date = datetime.strptime(form.scheduled_date.data, '%m/%d/%Y').date()
                    if work_order.scheduled_date != new_scheduled_date:
                        work_order.scheduled_date = new_scheduled_date
                        log_text = f'Scheduled date updated to {work_order.scheduled_date.strftime("%m/%d/%Y")}.'
                        db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
                        db.session.commit()
                        flash('Scheduled date updated.', 'success')
                    else:
                         return redirect(url_for('main.view_request', request_id=request_id)) # No change needed
                except ValueError:
                    flash('Invalid date format for scheduled date.', 'danger')
            return redirect(url_for('main.view_request', request_id=request_id)) # No status change

        work_order.status = new_status
        log_text = f'Changed status from {old_status} to {new_status}.'

        if new_status == 'Scheduled':
            # Store scheduled_date as Date object (no timezone)
            work_order.scheduled_date = datetime.strptime(form.scheduled_date.data, '%m/%d/%Y').date()
            log_text += f' for {work_order.scheduled_date.strftime("%m/%d/%Y")}' # Format date
        else:
            work_order.scheduled_date = None

        current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])
        if new_status == 'Closed':
            current_tags.add('Completed')
            work_order.date_completed = get_denver_now() # <-- Use Denver time
            log_text += " and tagged as 'Completed'."

        if old_status == 'Closed' and new_status != 'Closed':
            current_tags.discard('Completed')
            # Optionally clear date_completed if reopening? Decide based on workflow.
            # work_order.date_completed = None

        work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))

        if work_order.author and work_order.author != current_user:
            notification_text = f'Status for Request #{work_order.id} changed to {new_status}.'
            # Notification timestamp defaults to Denver time
            notification = Notification(
                text=notification_text,
                link=url_for('main.view_request', request_id=work_order.id),
                user_id=work_order.user_id
            )
            db.session.add(notification)

            send_push_notification(
                work_order.user_id,
                'Request Status Updated',
                notification_text,
                url_for('main.view_request', request_id=work_order.id, _external=True)
            )

            email_body = f"<p>The status of your Request #{work_order.id} for property <b>{work_order.property}</b> was changed from <b>{old_status}</b> to <b>{new_status}</b>.</p>"
            send_notification_email(
                subject=f"Status Update for Request #{work_order.id}",
                recipients=[work_order.author.email],
                text_body=notification_text,
                html_body=render_template(
                    'email/notification_email.html',
                    title="Request Status Updated",
                    user=work_order.author,
                    body_content=email_body,
                    link=url_for('main.view_request', request_id=work_order.id, _external=True)
                )
            )

        if new_status == 'Quote Sent' and work_order.property_manager:
            manager = User.query.filter_by(name=work_order.property_manager, role='Property Manager').first()
            if manager:
                notification_text = f'A quote has been sent for Request #{work_order.id} at {work_order.property}.'
                 # Notification timestamp defaults to Denver time
                manager_notification = Notification(
                    text=notification_text,
                    link=url_for('main.view_request', request_id=work_order.id),
                    user_id=manager.id)
                db.session.add(manager_notification)

                send_push_notification(
                    manager.id,
                    'Quote Approval Needed',
                    notification_text,
                    url_for('main.view_request', request_id=work_order.id, _external=True)
                )

                email_body_pm = f"<p>A quote has been sent and requires your approval for Request #{work_order.id} at property <b>{work_order.property}</b>.</p>"
                send_notification_email(
                    subject=f"Quote Approval Needed for Request #{work_order.id}",
                    recipients=[manager.email],
                    text_body=f"A quote requires your approval for Request #{work_order.id}.",
                    html_body=render_template(
                        'email/notification_email.html',
                        title="Quote Approval Needed",
                        user=manager,
                        body_content=email_body_pm,
                        link=url_for('main.view_request', request_id=work_order.id, _external=True)
                    )
                )
        db.session.commit()
        flash(f'Status updated to {new_status}.', 'success')
    else:
        # Log form errors for debugging
        current_app.logger.warning(f"ChangeStatusForm validation failed: {form.errors}")
        flash('Could not update status. Please check the form for errors.', 'danger')

    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/request/<int:request_id>/quote/<int:quote_id>/<action>', methods=['POST'])
@login_required
def quote_action(request_id, quote_id, action):
    work_order = WorkOrder.query.get_or_404(request_id)
    quote = Quote.query.get_or_404(quote_id)

    can_pm = current_user.role == 'Property Manager' and current_user.name == work_order.property_manager
    can_super_user = current_user.role == 'Super User'

    if not (can_pm or can_super_user):
        flash('You do not have permission to approve or decline quotes.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

    current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])

    if action == 'approve':
        quote.status = 'Approved'
        current_tags.add('Approved')
        current_tags.discard('Declined')
        # AuditLog timestamp defaults to Denver time
        log_text = f"Quote from {quote.vendor.company_name} approved."
        flash_text = f"Quote from {quote.vendor.company_name} has been approved."
    elif action == 'decline':
        quote.status = 'Declined'
        if not any(q.status == 'Approved' for q in work_order.quotes if q.id != quote.id):
            current_tags.discard('Approved')
            current_tags.add('Declined')
         # AuditLog timestamp defaults to Denver time
        log_text = f"Quote from {quote.vendor.company_name} declined."
        flash_text = f"Quote from {quote.vendor.company_name} has been declined."
    elif action == 'clear':
        quote.status = 'Pending'
        if not any(q.status == 'Approved' for q in work_order.quotes if q.id != quote.id):
            current_tags.discard('Approved')
        if not any(q.status == 'Declined' for q in work_order.quotes): # Changed logic slightly: Remove 'Declined' only if NO quotes are declined
            current_tags.discard('Declined')
        # AuditLog timestamp defaults to Denver time
        log_text = f"Status for quote from {quote.vendor.company_name} cleared."
        flash_text = f"Status for quote from {quote.vendor.company_name} has been cleared."
    else:
        return redirect(url_for('main.view_request', request_id=request_id))

    work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None # Ensure empty string isn't saved
    db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
    db.session.commit()
    flash(flash_text, 'success')
    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/tag_request/<int:request_id>', methods=['POST'])
@login_required
def tag_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = TagForm()

    can_pm = current_user.role == 'Property Manager' and current_user.name == work_order.property_manager
    can_super_user = current_user.role == 'Super User'

    can_remove_any_tag = current_user.role in ['Property Manager', 'Super User'] # Simplified permission

    if request.form.get('action') == 'remove_tag':
        remove_form = DeleteRestoreRequestForm() # Using for CSRF
        if remove_form.validate_on_submit():
            tag_to_remove = request.form.get('tag_to_remove')

            # Check permissions specifically for Approved/Declined
            if tag_to_remove in ['Approved', 'Declined'] and not (can_pm or can_super_user):
                flash(f"You do not have permission to remove the '{tag_to_remove}' tag.", 'danger')
                return redirect(url_for('main.view_request', request_id=request_id))

            current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])
            if tag_to_remove in current_tags:
                current_tags.remove(tag_to_remove)
                # If removing 'Follow-up needed', clear the date
                if tag_to_remove == 'Follow-up needed':
                    work_order.follow_up_date = None

                work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None # Ensure empty string isn't saved
                # AuditLog timestamp defaults to Denver time
                db.session.add(AuditLog(text=f"Tag '{tag_to_remove}' removed.", user_id=current_user.id, work_order_id=work_order.id))
                db.session.commit()
                flash(f"Tag '{tag_to_remove}' has been removed.", 'info')
        else:
            flash('Could not remove tag due to a security error.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

    # Filter choices based on role for adding tags
    if current_user.role in ['Admin', 'Scheduler']:
        form.tag.choices = [c for c in form.tag.choices if c[0] not in ['Approved', 'Declined']]
    # Property Managers and Super Users see all choices

    if form.validate_on_submit():
        tag_to_add = form.tag.data

        # Permission check already happened for choice filtering, but double-check critical tags
        if tag_to_add in ['Approved', 'Declined'] and not (can_pm or can_super_user):
            flash('You do not have permission to approve or decline requests.', 'danger')
            return redirect(url_for('main.view_request', request_id=request_id))

        current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])

        if tag_to_add == 'Follow-up needed':
            follow_up_date_str = form.follow_up_date.data
            if not follow_up_date_str:
                flash('A follow-up date is required when adding the "Follow-up needed" tag.', 'danger')
                return redirect(url_for('main.view_request', request_id=request_id))
            try:
                 # follow_up_date is Date type, no timezone
                work_order.follow_up_date = datetime.strptime(follow_up_date_str, '%m/%d/%Y').date()
            except ValueError:
                 flash('Invalid date format for follow-up date.', 'danger')
                 return redirect(url_for('main.view_request', request_id=request_id))

        # Handle conflicting tags
        if tag_to_add == 'Approved':
            current_tags.discard('Declined')
        elif tag_to_add == 'Declined':
            current_tags.discard('Approved')

        if tag_to_add not in current_tags:
            current_tags.add(tag_to_add)
            work_order.tag = ','.join(sorted(list(filter(None, current_tags))))
             # AuditLog timestamp defaults to Denver time
            db.session.add(AuditLog(text=f"Request tagged as '{tag_to_add}'.", user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash(f"Request has been tagged as '{tag_to_add}'.", 'success')
        else:
            # If tag exists but date needs updating (Follow-up)
            if tag_to_add == 'Follow-up needed' and work_order.follow_up_date != datetime.strptime(form.follow_up_date.data, '%m/%d/%Y').date():
                 work_order.follow_up_date = datetime.strptime(form.follow_up_date.data, '%m/%d/%Y').date()
                 db.session.add(AuditLog(text=f"Follow-up date updated for tag '{tag_to_add}'.", user_id=current_user.id, work_order_id=work_order.id))
                 db.session.commit()
                 flash(f"Follow-up date for tag '{tag_to_add}' updated.", 'success')
            else:
                 flash(f"Request is already tagged as '{tag_to_add}'.", 'info')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", 'danger')

    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/cancel_request/<int:request_id>', methods=['POST'])
@login_required
def cancel_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    is_author = work_order.author == current_user
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    # Allow Admin/Scheduler/SuperUser to cancel too
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']

    if not (is_author or is_property_manager or is_admin_staff):
        abort(403)
    if work_order.status in ['Closed', 'Cancelled']:
        flash('This request cannot be cancelled as it is already closed or cancelled.', 'warning')
        return redirect(url_for('main.view_request', request_id=request_id))

    work_order.status = 'Cancelled'
    work_order.tag = None # Clear tags on cancellation
    work_order.scheduled_date = None # Clear scheduled date
    work_order.follow_up_date = None # Clear follow-up date

    log_text = f'Request cancelled by {current_user.name} ({current_user.role})'
     # AuditLog timestamp defaults to Denver time
    db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
    db.session.commit()
    flash('The request has been successfully cancelled.', 'success')
    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/assign_vendor/<int:request_id>', methods=['POST'])
@login_required
@admin_required
def assign_vendor(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    vendor_id = request.form.get('vendor_id')

    if not vendor_id:
        flash('No vendor selected.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

    vendor = Vendor.query.get(vendor_id)
    if not vendor:
        flash('Invalid vendor selected.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

    work_order.vendor_id = vendor.id
     # AuditLog timestamp defaults to Denver time
    db.session.add(AuditLog(text=f"Vendor '{vendor.company_name}' assigned.", user_id=current_user.id, work_order_id=work_order.id))
    db.session.commit()
    flash(f"Vendor '{vendor.company_name}' has been assigned to this request.", 'success')
    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/unassign_vendor/<int:request_id>', methods=['POST'])
@login_required
@admin_required
def unassign_vendor(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = AssignVendorForm() # Use for CSRF validation
    if form.validate_on_submit(): # Check CSRF token
        if work_order.vendor:
            vendor_name = work_order.vendor.company_name
            work_order.vendor_id = None
            # AuditLog timestamp defaults to Denver time
            db.session.add(AuditLog(text=f"Vendor '{vendor_name}' unassigned.", user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash(f"Vendor '{vendor_name}' has been unassigned.", 'success')
        else:
            flash('No vendor was assigned to this request.', 'info')
    else:
        flash('Invalid request to unassign vendor.', 'danger')
    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/notifications/read/<int:notification_id>')
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.get_or_404(notification_id)
    if notification.user_id != current_user.id:
        abort(403)
    notification.is_read = True
    db.session.commit()
    return redirect(notification.link)

@main.route('/new-request', methods=['GET', 'POST'])
@login_required
def new_request():
    properties = Property.query.order_by(Property.name).all()
    properties_dict = {p.name: {"address": p.address, "manager": p.property_manager} for p in properties}
    form = NewRequestForm()
    form.request_type.choices = [(rt.id, rt.name) for rt in RequestType.query.order_by(RequestType.name).all()]

    if form.validate_on_submit():
        # Date fields are Date type, no timezone
        date1 = datetime.strptime(form.date_1.data, '%m/%d/%Y').date() if form.date_1.data else None
        date2 = datetime.strptime(form.date_2.data, '%m/%d/%Y').date() if form.date_2.data else None
        date3 = datetime.strptime(form.date_3.data, '%m/%d/%Y').date() if form.date_3.data else None

        selected_property = Property.query.filter_by(name=form.property.data).first()

        # WorkOrder creation, date_created defaults to Denver time via model
        new_order = WorkOrder(
            wo_number=form.wo_number.data, requester_name=current_user.name,
            request_type_id=form.request_type.data, description=form.description.data,
            property=form.property.data, unit=form.unit.data,
            tenant_name=form.tenant_name.data, tenant_phone=form.tenant_phone.data,
            contact_person=form.contact_person.data, contact_person_phone=form.contact_person_phone.data,
            preferred_date_1=date1, preferred_date_2=date2,
            preferred_date_3=date3, user_id=current_user.id,
            preferred_vendor=form.vendor_assigned.data
            )

        # Assign property details based on selection or lookup
        if selected_property:
            new_order.property_id = selected_property.id
            new_order.address = selected_property.address
            new_order.property_manager = selected_property.property_manager
        else:
            # Fallback for properties perhaps not yet in the DB but known via properties_dict (less likely now)
            new_order.address = properties_dict.get(form.property.data, {}).get('address', request.form.get('address', '')) # Get address from hidden input if needed
            new_order.property_manager = properties_dict.get(form.property.data, {}).get('manager', request.form.get('property_manager', '')) # Get manager from hidden input

        # Assign preferred vendor if found
        if form.vendor_assigned.data:
            vendor = Vendor.query.filter(Vendor.company_name.ilike(form.vendor_assigned.data)).first()
            if vendor:
                new_order.vendor_id = vendor.id

        db.session.add(new_order)
        db.session.commit() # Commit to get new_order.id

        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text='Request created.', user_id=current_user.id, work_order_id=new_order.id))
        db.session.commit() # Commit log

        # Save attachments
        for file in form.attachments.data:
            save_attachment(file, new_order.id)

        # Send notifications
        admins_and_schedulers = User.query.filter(User.role.in_(['Admin', 'Scheduler', 'Super User'])).all()
        for user in admins_and_schedulers:
            if user != current_user:
                notification_text = f'New request #{new_order.id} submitted by {current_user.name}.'
                # Notification timestamp defaults to Denver time
                notification = Notification(
                    text=notification_text,
                    link=url_for('main.view_request', request_id=new_order.id),
                    user_id=user.id
                )
                db.session.add(notification)
                send_push_notification(
                    user.id,
                    'New Work Request',
                    notification_text,
                    url_for('main.view_request', request_id=new_order.id, _external=True)
                )
        db.session.commit() # Commit notifications
        flash('Your request has been created!', 'success')
        return redirect(url_for('main.my_requests'))

    elif request.method == 'POST':
        current_app.logger.error(f"--- NEW REQUEST FORM VALIDATION FAILED --- Errors: {form.errors}")
        # Add flash messages for specific errors if helpful
        for field, errors in form.errors.items():
             for error in errors:
                  flash(f"Error in {getattr(form, field).label.text}: {error}", 'danger')


    return render_template('request_form.html', title='New Request', form=form,
        properties=properties, property_data=json.dumps(properties_dict))

@main.route('/edit-request/<int:request_id>', methods=['GET', 'POST'])
@login_required
def edit_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    is_author = work_order.author == current_user
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']

    if not (is_author or is_admin_staff):
        flash('You do not have permission to edit this request.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

    # Prevent editing closed/cancelled requests unless admin/super user
    if work_order.status in ['Closed', 'Cancelled'] and not current_user.role in ['Admin', 'Super User']:
        flash('This request cannot be edited because it is already closed or cancelled.', 'warning')
        return redirect(url_for('main.view_request', request_id=work_order.id))

    properties = Property.query.order_by(Property.name).all()
    properties_dict = {p.name: {"address": p.address, "manager": p.property_manager} for p in properties}
    form = NewRequestForm(obj=work_order) # Populate form from object
    form.request_type.choices = [(rt.id, rt.name) for rt in RequestType.query.order_by(RequestType.name).all()]
    reassign_form = ReassignRequestForm()

    del form.attachments # Remove attachments field from main edit form

    if form.validate_on_submit():
        # Update fields from form data
        work_order.wo_number = form.wo_number.data
        work_order.request_type_id = form.request_type.data
        work_order.description = form.description.data
        work_order.property = form.property.data # Property name string
        work_order.unit = form.unit.data
        work_order.tenant_name = form.tenant_name.data
        work_order.tenant_phone = form.tenant_phone.data
        work_order.contact_person = form.contact_person.data
        work_order.contact_person_phone = form.contact_person_phone.data
        work_order.preferred_vendor = form.vendor_assigned.data

        # Date fields are Date type, no timezone
        work_order.preferred_date_1 = datetime.strptime(form.date_1.data, '%m/%d/%Y').date() if form.date_1.data else None
        work_order.preferred_date_2 = datetime.strptime(form.date_2.data, '%m/%d/%Y').date() if form.date_2.data else None
        work_order.preferred_date_3 = datetime.strptime(form.date_3.data, '%m/%d/%Y').date() if form.date_3.data else None

        # Update property relation and details based on selected property name
        selected_property = Property.query.filter_by(name=form.property.data).first()
        if selected_property:
            work_order.property_id = selected_property.id
            work_order.address = selected_property.address
            work_order.property_manager = selected_property.property_manager
        else:
            # If property name doesn't match DB entry, clear relation and use submitted/looked up details
            work_order.property_id = None
            # Get address/manager potentially from hidden fields populated by JS
            work_order.address = request.form.get('address', properties_dict.get(form.property.data, {}).get('address', ''))
            work_order.property_manager = request.form.get('property_manager', properties_dict.get(form.property.data, {}).get('manager', ''))


        # Note: Attachments are handled separately via upload_attachment route

        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text='Edited request details.', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash('Request has been updated.', 'success')
        return redirect(url_for('main.view_request', request_id=work_order.id))

    elif request.method == 'POST':
        # Log validation errors if POST fails
        current_app.logger.error(f"--- EDIT FORM VALIDATION FAILED --- Errors: {form.errors}")
        for field, errors in form.errors.items():
             for error in errors:
                  flash(f"Error in {getattr(form, field).label.text}: {error}", 'danger')


    # Populate form with existing data on GET request
    if request.method == 'GET':
        form.wo_number.data = work_order.wo_number
        form.request_type.data = work_order.request_type_id
        form.description.data = work_order.description
        form.property.data = work_order.property
        form.unit.data = work_order.unit
        form.tenant_name.data = work_order.tenant_name
        form.tenant_phone.data = work_order.tenant_phone
        form.contact_person.data = work_order.contact_person
        form.contact_person_phone.data = work_order.contact_person_phone
        form.vendor_assigned.data = work_order.preferred_vendor
        form.date_1.data = work_order.preferred_date_1.strftime('%m/%d/%Y') if work_order.preferred_date_1 else ''
        form.date_2.data = work_order.preferred_date_2.strftime('%m/%d/%Y') if work_order.preferred_date_2 else ''
        form.date_3.data = work_order.preferred_date_3.strftime('%m/%d/%Y') if work_order.preferred_date_3 else ''

    return render_template('edit_request.html', title='Edit Request', form=form, work_order=work_order,
                           properties=properties, property_data=json.dumps(properties_dict), reassign_form=reassign_form)


@main.route('/upload_attachment/<int:request_id>', methods=['POST'])
@login_required
def upload_attachment(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = AttachmentForm() # AttachmentForm uses MultipleFileField named 'file'
    if form.validate_on_submit():
        files_uploaded_count = 0
        for file in form.file.data: # Access data attribute
            if file and file.filename:
                file_type = request.form.get('file_type', 'Attachment') # Check for optional file_type if needed elsewhere
                attachment_obj = save_attachment(file, request_id, file_type)

                if attachment_obj:
                    # AuditLog timestamp defaults to Denver time
                    db.session.add(AuditLog(text=f'Uploaded {file_type}: {secure_filename(file.filename)}', user_id=current_user.id, work_order_id=work_order.id))
                    files_uploaded_count += 1
                else:
                    # Log error if save_attachment failed for some reason
                    current_app.logger.error(f"Failed to save attachment object for file: {secure_filename(file.filename)}")
            # else: file might be empty if user selected multiple but one was blank

        if files_uploaded_count > 0:
             db.session.commit()
             flash(f'{files_uploaded_count} attachment(s) uploaded successfully.', 'success')
        else:
             flash('No valid files were selected or uploaded.', 'warning')

    else:
        # Log form validation errors
        current_app.logger.warning(f"AttachmentForm validation failed: {form.errors}")
        for field, errors in form.errors.items():
            for error_message in errors:
                flash(f"Attachment Error: {error_message}", 'danger')

    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/download_attachment/<int:attachment_id>')
@login_required
def download_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    work_order = WorkOrder.query.get(attachment.work_order_id)
    if not work_order:
         abort(404)

    # Permission check (allow author, viewers, PM, admin staff)
    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']

    if not (is_author or is_viewer or is_property_manager or is_admin_staff):
        abort(403)

    # Make sure the filename in the header is safe
    safe_display_name = secure_filename(attachment.filename) # Use the stored unique name initially
    # You might want to store the original filename too if you want to offer that for download
    # For now, we use the unique ID name

    return send_from_directory(current_app.config['UPLOAD_FOLDER'], attachment.filename, as_attachment=True, download_name=safe_display_name)

@main.route('/view_attachment/<int:attachment_id>')
@login_required
def view_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    work_order = WorkOrder.query.get(attachment.work_order_id)
    if not work_order:
        abort(404)

    # Permission check (allow author, viewers, PM, admin staff)
    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']

    if not (is_author or is_viewer or is_property_manager or is_admin_staff):
        abort(403)

    # Determine mimetype
    mimetype, _ = mimetypes.guess_type(attachment.filename)
    if not mimetype: # Provide a default if guess fails
        mimetype = 'application/octet-stream'

    return send_from_directory(current_app.config['UPLOAD_FOLDER'], attachment.filename, as_attachment=False, mimetype=mimetype)


@main.route('/delete_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    # Allow uploader or Admin/Super User to delete
    if attachment.user_id != current_user.id and current_user.role not in ['Admin', 'Super User']:
        abort(403)

    form = DeleteRestoreRequestForm() # Use for CSRF validation
    if form.validate_on_submit():
        original_filename = attachment.filename # Keep for logging
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment.filename)
        work_order_id = attachment.work_order_id # Keep for redirect and logging

        try:
            # Delete physical file
            if os.path.exists(file_path):
                os.remove(file_path)
            else:
                 current_app.logger.warning(f"File not found for deletion: {file_path}")

            # AuditLog timestamp defaults to Denver time
            db.session.add(AuditLog(text=f'Deleted attachment: {original_filename}', user_id=current_user.id, work_order_id=work_order_id))
            # Delete DB record
            db.session.delete(attachment)
            db.session.commit()
            flash('Attachment deleted.', 'success')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error deleting attachment {attachment_id}: {e}", exc_info=True)
            flash('Error deleting attachment.', 'danger')
    else:
        flash('Invalid request to delete attachment.', 'danger')

    return redirect(url_for('main.view_request', request_id=work_order_id))


@main.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    update_form = UpdateAccountForm(obj=current_user)
    password_form = ChangePasswordForm()

    if 'update_account' in request.form and update_form.validate_on_submit():
        current_user.name = update_form.name.data
        current_user.email = update_form.email.data

        if current_user.role in ['Admin', 'Scheduler', 'Super User', 'Property Manager']:
            # ... (keep signature cleaning and embedding logic) ...
            allowed_tags = [
                'a', 'abbr', 'acronym', 'b', 'blockquote', 'code', 'em', 'i', 'strong',
                'li', 'ol', 'ul', 'br', 'p', 'img', 'span', 'div', 'font',
                'table', 'tbody', 'thead', 'tr', 'td', 'th', 'figure', 'figcaption'
            ]
            allowed_attrs = {
                '*': ['style', 'class', 'align', 'valign', 'width', 'height', 'cellpadding', 'cellspacing', 'border'],
                'a': ['href', 'title', 'target'],
                'img': ['src', 'alt', 'width', 'height', 'style'],
                'font': ['color', 'face', 'size']
            }

            signature_html = request.form.get('signature', '') # Default to empty string

            def embed_local_images(html_content):
                # ... (embedding logic remains the same) ...
                 upload_folder = current_app.config['UPLOAD_FOLDER']
                 # Regex to find image URLs pointing back to our own /uploads/ endpoint
                 # It captures the full URL and the filename part after /uploads/
                 img_tags = re.findall(r'<img[^>]+src=[\'"](https?://[^/]+/uploads/([^\'"]+))[\'"]', html_content)

                 for full_url, filename_part in img_tags:
                      # Remove potential query parameters like ?v=timestamp from the filename
                      filename = filename_part.split('?')[0]
                      filepath = os.path.join(upload_folder, filename)

                      if os.path.exists(filepath):
                           try:
                                with open(filepath, "rb") as image_file:
                                     encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

                                mime_type, _ = mimetypes.guess_type(filepath)
                                if not mime_type:
                                     # Guess common image types based on extension if mimetypes fails
                                     ext = os.path.splitext(filename)[1].lower()
                                     if ext == '.png': mime_type = 'image/png'
                                     elif ext in ['.jpg', '.jpeg']: mime_type = 'image/jpeg'
                                     elif ext == '.gif': mime_type = 'image/gif'
                                     elif ext == '.webp': mime_type = 'image/webp'
                                     else: mime_type = 'application/octet-stream' # Fallback

                                data_uri = f"data:{mime_type};base64,{encoded_string}"

                                # Replace the original URL with the data URI
                                # Use full_url to ensure we replace the exact match
                                html_content = html_content.replace(full_url, data_uri, 1)
                           except Exception as e:
                                current_app.logger.error(f"Error embedding image {filename}: {e}")

                 return html_content


            embedded_html = embed_local_images(signature_html)

            # Clean the HTML (including data URIs)
            clean_html = bleach.clean(
                embedded_html,
                tags=allowed_tags,
                attributes=allowed_attrs,
                protocols=['http', 'https', 'mailto', 'data'] # Ensure 'data' protocol is allowed for embedded images
            )
            current_user.signature = clean_html

        db.session.commit()
        flash('Your account has been updated!', 'success')
        return redirect(url_for('main.account'))
    elif 'update_account' in request.form:
         # Log validation errors if update fails
         current_app.logger.warning(f"UpdateAccountForm validation failed: {update_form.errors}")
         for field, errors in update_form.errors.items():
            for error in errors:
                flash(f"Update Error: {error}", 'danger')


    if 'change_password' in request.form and password_form.validate_on_submit():
        if current_user.check_password(password_form.current_password.data):
            current_user.set_password(password_form.new_password.data)
            db.session.commit()
            flash('Your password has been changed!', 'success')
        else:
            flash('Incorrect current password.', 'danger')
        return redirect(url_for('main.account')) # Redirect even on failure to clear form
    elif 'change_password' in request.form:
         # Log validation errors if password change fails
         current_app.logger.warning(f"ChangePasswordForm validation failed: {password_form.errors}")
         # Don't flash password-specific errors usually, just the incorrect password one
         if 'current_password' not in password_form.errors: # Only flash if it's not the 'incorrect password' case
              flash('Password change failed due to validation errors.', 'danger')


    return render_template('account.html', title='Account', update_form=update_form, password_form=password_form)


@main.route('/upload_image', methods=['POST'])
@csrf.exempt # Exempt CSRF for CKEditor SimpleUpload adapter
@login_required
def upload_image():
    if 'upload' in request.files:
        file = request.files['upload']
        # Add basic validation (e.g., file type, size)
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        if file and '.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions:
            try:
                filename = secure_filename(file.filename)
                ext = filename.rsplit('.', 1)[1].lower()
                # Create a unique filename to avoid collisions
                unique_filename = f"{uuid.uuid4().hex}.{ext}"
                filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)

                # Generate URL for the uploaded file
                # Add a timestamp as a query parameter to help bypass browser cache if needed
                cache_buster = int(get_denver_now().timestamp())
                url = url_for('main.uploaded_file', filename=unique_filename, v=cache_buster, _external=True)

                return jsonify({'uploaded': 1, 'fileName': unique_filename, 'url': url})
            except Exception as e:
                current_app.logger.error(f"Error saving uploaded image: {e}", exc_info=True)
                return jsonify({'uploaded': 0, 'error': {'message': 'Server error during upload.'}}), 500
        else:
            return jsonify({'uploaded': 0, 'error': {'message': 'Invalid file type.'}}), 400
    return jsonify({'uploaded': 0, 'error': {'message': 'No upload file found.'}}), 400

@main.route('/uploads/<filename>')
def uploaded_file(filename):
    # Basic security check: ensure filename doesn't try to access parent directories
    if '..' in filename or filename.startswith('/'):
        abort(404)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

@main.route('/reports')
@login_required
@admin_required
def reports_page():
    form = ReportForm()
    return render_template('reports.html', title='Reports', form=form)

@main.route('/reports/download/all_work_orders')
@login_required
@admin_required
def download_all_work_orders():
    date_type = request.args.get('date_type', 'date_created')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    query = WorkOrder.query

    start_dt, end_dt = get_date_range(request.args.get('date_range'), start_date_str, end_date_str)

    # Filter based on the chosen DateTime column (date_created or date_completed)
    date_column = getattr(WorkOrder, date_type, WorkOrder.date_created) # Default to date_created if invalid
    if start_dt:
        query = query.filter(date_column >= start_dt)
    if end_dt:
        query = query.filter(date_column <= end_dt)

    work_orders = query.order_by(WorkOrder.date_created.asc()).all()

    string_io = io.StringIO()
    csv_writer = csv.writer(string_io)
    headers = [
        'ID', 'WO Number', 'Status', 'Tag', 'Vendor Assigned', 'Date Created', 'Date Completed', 'Requester', 'Request Type',
        'Property', 'Unit', 'Address', 'Description'
    ]
    csv_writer.writerow(headers)
    for wo in work_orders:
        # Convert DB times (now potentially Denver-aware) to Denver before formatting
        created_dt = convert_to_denver(wo.date_created)
        completed_dt = convert_to_denver(wo.date_completed)
        # Format using mm/dd/yyyy HH:MM
        created_str = created_dt.strftime('%m/%d/%Y %H:%M') if created_dt else ''
        completed_str = completed_dt.strftime('%m/%d/%Y %H:%M') if completed_dt else ''

        csv_writer.writerow([
            wo.id, wo.wo_number, wo.status, wo.tag, wo.vendor.company_name if wo.vendor else '',
            created_str,
            completed_str,
            wo.requester_name, wo.request_type_relation.name, wo.property, wo.unit,
            wo.address, wo.description
        ])

    output = string_io.getvalue()
    string_io.close()

    # Generate filename with date range info if applicable
    filename_suffix = date_type
    if start_dt and end_dt:
         filename_suffix += f"_{start_dt.strftime('%Y%m%d')}-{end_dt.strftime('%Y%m%d')}"
    elif start_dt:
         filename_suffix += f"_from_{start_dt.strftime('%Y%m%d')}"

    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=work_orders_{filename_suffix}.csv"}
    )

@main.route('/reports/download/summary')
@login_required
@admin_required
def download_summary_report():
    date_type = request.args.get('date_type', 'date_created')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    start_dt, end_dt = get_date_range(request.args.get('date_range'), start_date_str, end_date_str)

    base_query = WorkOrder.query
    date_column = getattr(WorkOrder, date_type, WorkOrder.date_created) # Default safely

    if start_dt:
        base_query = base_query.filter(date_column >= start_dt)
    if end_dt:
        base_query = base_query.filter(date_column <= end_dt)

    filtered_orders = base_query.all()

    status_counts = Counter(req.status for req in filtered_orders)
    type_counts = Counter(req.request_type_relation.name for req in filtered_orders)
    property_counts = Counter(req.property for req in filtered_orders)

    string_io = io.StringIO()
    csv_writer = csv.writer(string_io)
    csv_writer.writerow(['Summary by Status'])
    csv_writer.writerow(['Status', 'Count'])
    for status, count in status_counts.items():
        csv_writer.writerow([status, count])
    csv_writer.writerow([]) # Blank row separator
    csv_writer.writerow(['Summary by Request Type'])
    csv_writer.writerow(['Type', 'Count'])
    for req_type, count in type_counts.items():
        csv_writer.writerow([req_type, count])
    csv_writer.writerow([])
    csv_writer.writerow(['Summary by Property'])
    csv_writer.writerow(['Property', 'Count'])
    for prop, count in property_counts.items():
        csv_writer.writerow([prop, count])

    output = string_io.getvalue()
    string_io.close()

    # Generate filename with date range info
    filename_suffix = date_type
    if start_dt and end_dt:
         filename_suffix += f"_{start_dt.strftime('%Y%m%d')}-{end_dt.strftime('%Y%m%d')}"
    elif start_dt:
         filename_suffix += f"_from_{start_dt.strftime('%Y%m%d')}"

    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=summary_report_{filename_suffix}.csv"}
    )

@main.route('/calendar')
@login_required
def calendar():
    return render_template('calendar.html', title='Calendar')

@main.route('/api/events')
@login_required
def api_events():
    status_colors = {
        'Scheduled': '#8B5CF6',  # purple
        'New': '#3B82F6',        # blue
        'Open': '#10B981',       # green
        'Pending': '#F59E0B',    # yellow
        'Follow-up': '#EF4444'   # red
    }

    # Date fields (scheduled_date, follow_up_date) are naive Date objects in the DB
    query = WorkOrder.query.filter(WorkOrder.scheduled_date.isnot(None), WorkOrder.is_deleted==False)
    if current_user.role == 'Requester':
        query = query.filter(WorkOrder.user_id == current_user.id)
    events_scheduled = query.all()

    event_list = []
    for event in events_scheduled:
        event_list.append({
            'title': f"#{event.id} - {event.property}",
            'start': event.scheduled_date.strftime('%Y-%m-%d'), # FullCalendar prefers ISO format for dates
            'url': url_for('main.view_request', request_id=event.id),
            'color': status_colors.get(event.status, '#6B7280'), # Default to gray
            'extendedProps': {
                'requester': event.requester_name,
                'status': event.status,
                'type': 'Scheduled'
            }
        })

    follow_up_events_query = WorkOrder.query.filter(WorkOrder.follow_up_date.isnot(None), WorkOrder.is_deleted==False)
    if current_user.role == 'Requester':
         follow_up_events_query = follow_up_events_query.filter(WorkOrder.user_id == current_user.id)
    follow_up_events = follow_up_events_query.all()

    for event in follow_up_events:
        event_list.append({
            'title': f"Follow-up for #{event.id}",
            'start': event.follow_up_date.strftime('%Y-%m-%d'), # FullCalendar prefers ISO format for dates
            'url': url_for('main.view_request', request_id=event.id),
            'color': status_colors.get('Follow-up'),
            'extendedProps': {
                'requester': event.requester_name,
                'status': event.status,
                'type': 'Follow-up'
            }
        })
    return jsonify(event_list)

@main.route('/api/vendors/search')
@login_required
def search_vendors():
    q = request.args.get('q')
    if q:
        vendors = Vendor.query.filter(Vendor.company_name.ilike(f'%{q}%')).limit(20).all() # Add limit
        return jsonify([{'id': v.id, 'company_name': v.company_name, 'contact_name': v.contact_name, 'email': v.email, 'phone': v.phone, 'specialty': v.specialty, 'website': v.website} for v in vendors])
    return jsonify([])

@main.route('/api/users/search')
@login_required
def api_user_search():
    users = User.query.filter_by(is_active=True).order_by(User.name).all()
    # Format for Tribute.js: 'key' is display/insert, 'value' is for lookup matching
    user_list = [{'key': user.name, 'value': user.name.replace(' ', '')} for user in users]
    return jsonify(user_list)

@main.route('/request/<int:request_id>/send_email', methods=['POST'])
@login_required
@admin_required
def send_work_order_email(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    recipient = request.form.get('recipient')
    cc = request.form.get('cc')
    subject = request.form.get('subject')
    body = request.form.get('body') # This is HTML from CKEditor
    files = request.files.getlist('attachments')

    if not recipient:
        return jsonify({'success': False, 'message': 'Recipient email is required.'}), 400

    recipients = [recipient]
    cc_list = [email.strip() for email in cc.split(',')] if cc else []

    attachments_for_email = []
    temp_upload_path = None
    if files:
        # Create a unique temporary directory for this email's attachments
        temp_dir_name = f"temp_email_{uuid.uuid4().hex}"
        temp_upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], temp_dir_name)
        try:
            os.makedirs(temp_upload_path, exist_ok=True)
            for file in files:
                if file and file.filename:
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(temp_upload_path, filename) # Use original filename within temp dir
                    file.save(filepath)
                    attachments_for_email.append({
                        'path': filepath,
                        'filename': filename,
                        'mimetype': file.mimetype or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                    })
        except Exception as e:
             current_app.logger.error(f"Error saving temporary email attachments: {e}", exc_info=True)
             # Clean up any files already saved in temp dir if creation fails mid-way
             if temp_upload_path and os.path.exists(temp_upload_path):
                 import shutil
                 shutil.rmtree(temp_upload_path, ignore_errors=True)
             return jsonify({'success': False, 'message': 'Error preparing attachments.'}), 500


    # Generate text version from HTML body
    text_version_of_body = bleach.clean(body, tags=[], strip=True).strip()
    # Pass necessary context to text template
    text_body = render_template('email/work_order_email.txt', work_order=work_order, body=text_version_of_body)
    # HTML body comes directly from CKEditor
    html_body = render_template('email/work_order_email.html', work_order=work_order, body=body)

    try:
        send_notification_email(
            subject=subject,
            recipients=recipients,
            cc=cc_list,
            text_body=text_body,
            html_body=html_body,
            attachments=attachments_for_email
        )

        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text=f"Work order emailed to {recipient}", user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()

        return jsonify({'success': True, 'message': 'Email sent successfully!'})

    except Exception as e:
        current_app.logger.error(f"Error sending email or committing log: {e}", exc_info=True)
        db.session.rollback() # Rollback audit log if email fails
        return jsonify({'success': False, 'message': 'Failed to send email.'}), 500

    finally:
        # Clean up temporary attachment directory and files
        if temp_upload_path and os.path.exists(temp_upload_path):
             import shutil
             shutil.rmtree(temp_upload_path, ignore_errors=True)
             current_app.logger.debug(f"Cleaned up temporary directory: {temp_upload_path}")


@main.route('/request/<int:request_id>/add_quote', methods=['POST'])
@login_required
@admin_required
def add_quote(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = QuoteForm()
    if form.validate_on_submit():
        vendor = form.vendor.data
        file = form.quote_file.data
        attachment_obj = save_attachment(file, work_order.id, file_type='Quote')

        if attachment_obj:
            # Quote creation, date_sent defaults to Denver time
            quote = Quote(
                work_order_id=work_order.id,
                vendor_id=vendor.id,
                attachment_id=attachment_obj.id
            )
            db.session.add(quote)
            # AuditLog timestamp defaults to Denver time
            db.session.add(AuditLog(text=f"Quote '{secure_filename(file.filename)}' for vendor '{vendor.company_name}' uploaded.", user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash(f'Quote for {vendor.company_name} uploaded successfully.', 'success')
        else:
            flash('There was an error saving the quote file.', 'danger')
    else:
        for field, errors in form.errors.items():
            for error_message in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error_message}", 'danger')
    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/request/delete_quote/<int:quote_id>', methods=['POST'])
@login_required
@admin_required
def delete_quote(quote_id):
    quote = Quote.query.get_or_404(quote_id)
    work_order_id = quote.work_order_id
    attachment = Attachment.query.get(quote.attachment_id)
    form = DeleteRestoreRequestForm() # Use for CSRF

    if form.validate_on_submit():
        if attachment:
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment.filename)
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                else:
                    current_app.logger.warning(f"Quote file not found for deletion: {file_path}")

                db.session.delete(attachment) # Delete attachment record first

            except OSError as e:
                current_app.logger.error(f"Error deleting quote file {attachment.filename}: {e}")
                # Don't necessarily stop the quote deletion if file removal fails
                pass

        vendor_name = quote.vendor.company_name if quote.vendor else 'N/A'

        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text=f"Deleted quote from vendor '{vendor_name}'.", user_id=current_user.id, work_order_id=work_order_id))

        db.session.delete(quote) # Delete quote record
        db.session.commit()

        flash('Quote has been deleted successfully.', 'success')
    else:
        flash('Invalid request to delete quote.', 'danger')

    return redirect(url_for('main.view_request', request_id=work_order_id))


def send_reminders():
    # Use Denver's current date for comparison
    today = get_denver_now().date() # <-- Get Denver's date
    work_orders_for_follow_up = WorkOrder.query.filter(
        WorkOrder.follow_up_date <= today, # Comparing Date to Date is fine
        WorkOrder.tag.like('%Follow-up needed%'),
        WorkOrder.is_deleted == False # Added check for deleted orders
    ).all()

    current_app.logger.info(f"Found {len(work_orders_for_follow_up)} work orders for follow-up reminder.")

    for wo in work_orders_for_follow_up:
        current_app.logger.info(f"Processing reminder for WO #{wo.id}")
        admins_and_schedulers = User.query.filter(User.role.in_(['Admin', 'Scheduler', 'Super User'])).all()
        for user in admins_and_schedulers:
            notification_text = f"Follow-up reminder for Request #{wo.id}"
            # Notification timestamp defaults to Denver time
            notification = Notification(
                text=notification_text,
                link=url_for('main.view_request', request_id=wo.id),
                user_id=user.id
            )
            db.session.add(notification)
            current_app.logger.info(f"Added notification for user {user.id} for WO #{wo.id}")

            send_push_notification(
                user.id,
                'Follow-up Reminder',
                notification_text,
                url_for('main.view_request', request_id=wo.id, _external=True)
            )

            email_body = f"<p>This is a reminder to follow-up on Request #{wo.id} for property <b>{wo.property}</b>.</p>"
            send_notification_email(
                subject=f"Follow-up Reminder for Request #{wo.id}",
                recipients=[user.email],
                text_body=notification_text,
                html_body=render_template(
                    'email/notification_email.html',
                    title="Follow-up Reminder",
                    user=user,
                    body_content=email_body,
                    link=url_for('main.view_request', request_id=wo.id, _external=True)
                )
            )

        # Remove tag and date after sending notifications
        current_tags = set(wo.tag.split(',') if wo.tag and wo.tag.strip() else [])
        current_tags.discard('Follow-up needed')
        wo.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
        wo.follow_up_date = None
        # Try to get Super User ID for audit log, default to a system/admin ID if needed
        audit_user = User.query.filter_by(role='Super User').first()
        # Fallback needed if no Super User exists (e.g., during setup)
        audit_user_id = audit_user.id if audit_user else 1 # Assuming user ID 1 is an admin/system user
        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text="Follow-up reminder sent and tag removed.", user_id=audit_user_id, work_order_id=wo.id))
        current_app.logger.info(f"Removed follow-up tag and date for WO #{wo.id}")

    # Commit all changes after the loop
    try:
        db.session.commit()
        current_app.logger.info("Committed reminder updates.")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error committing reminder updates: {e}", exc_info=True)


@main.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    current_app.logger.info(f"DEBUG SUB: --- !!! ENTERED /subscribe route !!! --- User: {getattr(current_user, 'name', 'anonymous')}")
    # Log helpful debug info for troubleshooting
    current_app.logger.debug(f"DEBUG SUB: Request headers: {dict(request.headers)}")
    current_app.logger.debug(f"DEBUG SUB: X-CSRFToken header: {request.headers.get('X-CSRFToken')}")
    current_app.logger.debug(f"DEBUG SUB: request.is_json: {request.is_json}")
    current_app.logger.debug(f"DEBUG SUB: current_user.is_authenticated: {current_user.is_authenticated}")

    subscription_data = None
    try:
        subscription_data = request.get_json()
    except Exception as e:
        current_app.logger.warning(f"DEBUG SUB: Exception parsing JSON body: {e}")
    if not subscription_data:
        current_app.logger.warning("DEBUG SUB: Subscription endpoint called with no data.")
        return jsonify({'success': False, 'message': 'No subscription data received.'}), 400

    # Basic validation of subscription data structure
    if not isinstance(subscription_data, dict) or 'endpoint' not in subscription_data:
         current_app.logger.warning(f"DEBUG SUB: Invalid subscription data structure: {subscription_data}")
         return jsonify({'success': False, 'message': 'Invalid subscription data structure.'}), 400


    subscription_json = json.dumps(subscription_data)
    subscription = PushSubscription.query.filter_by(
        subscription_json=subscription_json,
        user_id=current_user.id
    ).first()

    if not subscription:
        current_app.logger.info(f"DEBUG SUB: New subscription for user {current_user.name}. Saving to DB.")
        try:
            new_subscription = PushSubscription(
                subscription_json=subscription_json,
                user_id=current_user.id
            )
            db.session.add(new_subscription)
            db.session.commit()
            current_app.logger.info(f"DEBUG SUB: Successfully saved new subscription for user {current_user.name}.")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"DEBUG SUB: Error saving subscription to DB: {e}", exc_info=True)
            return jsonify({'success': False, 'message': 'Error saving subscription.'}), 500
    else:
        current_app.logger.info(f"DEBUG SUB: Subscription already exists for user {current_user.name}.")

    return jsonify({'success': True})


@main.route('/vapid_public_key', methods=['GET'])
def vapid_public_key():
    """Return the VAPID public key as JSON so the client can fetch it at runtime."""
    key = current_app.config.get('VAPID_PUBLIC_KEY')
    if not key:
        current_app.logger.warning('DEBUG SUB: VAPID_PUBLIC_KEY requested but not configured.')
        return jsonify({'success': False, 'message': 'VAPID public key not configured.'}), 500
    current_app.logger.debug('DEBUG SUB: VAPID_PUBLIC_KEY served to client')
    return jsonify({'success': True, 'vapidPublicKey': key})


@main.route('/test_push', methods=['GET'])
@login_required
def test_push():
    """Trigger a test push notification to the current user's subscriptions."""
    try:
        title = 'Test Notification'
        body = f'This is a test push to {current_user.name}. Time: {get_denver_now().strftime("%I:%M:%S %p")}'
        link = url_for('main.index', _external=True)
        send_push_notification(current_user.id, title, body, link)
        flash('Test push notification sent (or attempted). Check your device and server logs.', 'info')
        # Return simple success page or redirect
        # return jsonify({'success': True, 'message': 'Test push sent (server attempted sends). Check server logs for results.'})
        return redirect(request.referrer or url_for('main.index'))
    except Exception as e:
        current_app.logger.error(f"ERROR in test_push: {e}", exc_info=True)
        flash(f'Error sending test push: {e}', 'danger')
        # return jsonify({'success': False, 'message': str(e)}), 500
        return redirect(request.referrer or url_for('main.index'))


@main.route('/my_subscriptions', methods=['GET'])
@login_required
def my_subscriptions():
    """Return the current user's stored push subscriptions (for debugging)."""
    try:
        subs = PushSubscription.query.filter_by(user_id=current_user.id).all()
        out = []
        for s in subs:
            try:
                parsed = json.loads(s.subscription_json)
                endpoint_short = parsed.get('endpoint', 'N/A')[:60] + '...' if parsed.get('endpoint') else 'N/A'
            except Exception:
                parsed = {'error': 'Could not parse JSON'}
                endpoint_short = 'Error parsing'

            out.append({
                'id': s.id,
                'endpoint_short': endpoint_short,
                'keys': parsed.get('keys', {}) if isinstance(parsed, dict) else {},
                # 'raw': s.subscription_json # Optionally include raw for deep debugging
                })
        # Render a simple HTML page instead of just JSON
        return render_template('debug/subscriptions.html', title='My Push Subscriptions', subscriptions=out)
        # return jsonify({'success': True, 'subscriptions': out})
    except Exception as e:
        current_app.logger.error(f"ERROR in my_subscriptions: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@main.route('/subscriptions/<int:sub_id>', methods=['POST']) # Changed to POST for CSRF
@login_required
def delete_subscription(sub_id):
    """Delete a stored push subscription by id (authorized for the owner only)."""
    # Use a simple form for CSRF protection
    form = DeleteRestoreRequestForm()
    if form.validate_on_submit():
        try:
            sub = PushSubscription.query.get_or_404(sub_id)
            if sub.user_id != current_user.id:
                 flash('Not authorized to delete this subscription.', 'danger')
                 return redirect(url_for('main.my_subscriptions'))
                 # return jsonify({'success': False, 'message': 'Not authorized.'}), 403

            db.session.delete(sub)
            db.session.commit()
            flash(f'Subscription {sub_id} deleted successfully.', 'success')
            return redirect(url_for('main.my_subscriptions'))
            # return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"ERROR deleting subscription {sub_id}: {e}", exc_info=True)
            flash(f'Error deleting subscription: {e}', 'danger')
            return redirect(url_for('main.my_subscriptions'))
            # return jsonify({'success': False, 'message': str(e)}), 500
    else:
         flash('Invalid request to delete subscription.', 'danger')
         return redirect(url_for('main.my_subscriptions'))