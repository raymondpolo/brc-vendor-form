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
# Ensure TagForm is imported correctly
from app.forms import (NoteForm, ChangeStatusForm, AttachmentForm, NewRequestForm,
                       UpdateAccountForm, ChangePasswordForm, AssignVendorForm, ReportForm,
                       QuoteForm, DeleteRestoreRequestForm, TagForm, ReassignRequestForm,
                       SendFollowUpForm, MarkAsCompletedForm, GoBackForm)
from app.email import send_notification_email
from werkzeug.utils import secure_filename
from app.decorators import admin_required, role_required
from app.events import broadcast_new_note
from app.utils import get_denver_now, convert_to_denver, make_denver_aware_start_of_day, make_denver_aware_end_of_day, format_app_dt # Import helpers


def get_requester_initials(name):
    """Generates initials from a name string."""
    parts = name.split()
    if len(parts) > 1:
        return (parts[0][0] + parts[-1][0]).upper()
    elif parts:
        # Use first two letters if only one name part
        return parts[0][:2].upper()
    return "" # Return empty string if name is empty or None

def save_attachment(file, work_order_id, file_type='Attachment'):
    """Saves an uploaded file with a unique name and creates an Attachment record."""
    if not file or not file.filename:
        return None
    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    # Generate a unique filename using UUID to prevent collisions and obscure original names
    unique_filename = f"{uuid.uuid4().hex}.{ext}"
    try:
        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename))
        # Create and save the Attachment record
        attachment = Attachment(
            filename=unique_filename,
            user_id=current_user.id,
            work_order_id=work_order_id,
            file_type=file_type
        )
        db.session.add(attachment)
        db.session.commit()
        return attachment
    except Exception as e:
        current_app.logger.error(f"Error saving attachment {filename}: {e}", exc_info=True)
        db.session.rollback() # Rollback DB changes if file saving fails
        return None


def work_order_to_dict(req):
    """Helper function to convert a WorkOrder object to a dictionary for JSON serialization."""
    # Convert date_created to Denver time before formatting if needed
    denver_created_date = convert_to_denver(req.date_created)
    request_type_name = req.request_type_relation.name if req.request_type_relation else 'N/A'
    return {
        'id': req.id,
        # Keep legacy short date for compact UIs
        'date_created': denver_created_date.strftime('%m/%d/%Y') if denver_created_date else '',
        # Add explicit app-local formatted datetime and ISO-like string for APIs
        'date_created_local': denver_created_date.strftime('%m/%d/%Y %I:%M %p') if denver_created_date else '',
        'date_created_iso': format_app_dt(req.date_created) if req.date_created else None,
        'wo_number': req.wo_number,
        'requester_name': req.requester_name,
        'property': req.property,
        'unit': req.unit,
        'address': req.address,
        'property_manager': req.property_manager,
        'status': req.status,
        'request_type': request_type_name,
        'tag': req.tag,
        'vendor_name': req.vendor.company_name if req.vendor else 'N/A'
    }


def get_date_range(range_key, start_date_str=None, end_date_str=None):
    """Return (start_dt, end_dt) as timezone-aware datetimes based on range_key or explicit strings.

    - range_key: optional value like 'last_7', 'this_month', or 'custom'
    - start_date_str / end_date_str: strings in '%m/%d/%Y' when provided
    Returns (start_dt, end_dt) where each may be None.
    """
    try:
        start_dt = None
        end_dt = None

        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%m/%d/%Y').date()
            start_dt = make_denver_aware_start_of_day(start_date)

        if end_date_str:
            end_date = datetime.strptime(end_date_str, '%m/%d/%Y').date()
            end_dt = make_denver_aware_end_of_day(end_date)

        # Handle simple named ranges only if no custom dates were provided
        if not start_dt and not end_dt and range_key and range_key != 'all':
            now = get_denver_now()
            today = now.date()
            if range_key == 'today':
                 start_dt = make_denver_aware_start_of_day(today)
                 end_dt = make_denver_aware_end_of_day(today)
            elif range_key == 'yesterday':
                 yesterday = today - timedelta(days=1)
                 start_dt = make_denver_aware_start_of_day(yesterday)
                 end_dt = make_denver_aware_end_of_day(yesterday)
            elif range_key == 'this_week':
                 start_of_week = today - timedelta(days=today.weekday())
                 start_dt = make_denver_aware_start_of_day(start_of_week)
                 end_dt = make_denver_aware_end_of_day(today) # Up to end of today
            elif range_key == 'last_week':
                 end_of_last_week = today - timedelta(days=today.weekday() + 1)
                 start_of_last_week = end_of_last_week - timedelta(days=6)
                 start_dt = make_denver_aware_start_of_day(start_of_last_week)
                 end_dt = make_denver_aware_end_of_day(end_of_last_week)
            elif range_key == 'this_month':
                start_dt = make_denver_aware_start_of_day(today.replace(day=1))
                end_dt = make_denver_aware_end_of_day(today) # Up to end of today
            elif range_key == 'last_month':
                 first_of_this_month = today.replace(day=1)
                 last_of_last_month = first_of_this_month - timedelta(days=1)
                 first_of_last_month = last_of_last_month.replace(day=1)
                 start_dt = make_denver_aware_start_of_day(first_of_last_month)
                 end_dt = make_denver_aware_end_of_day(last_of_last_month)
            elif range_key == 'this_year':
                 start_dt = make_denver_aware_start_of_day(today.replace(month=1, day=1))
                 end_dt = make_denver_aware_end_of_day(today) # Up to end of today
            elif range_key == 'last_year':
                 last_day_last_year = today.replace(year=today.year - 1, month=12, day=31)
                 first_day_last_year = last_day_last_year.replace(month=1, day=1)
                 start_dt = make_denver_aware_start_of_day(first_day_last_year)
                 end_dt = make_denver_aware_end_of_day(last_day_last_year)
            elif range_key == 'custom_date': # Handle single custom date
                 if start_dt: # Use start_dt if provided for the custom date
                      end_dt = make_denver_aware_end_of_day(start_dt.date())
                 else: # Invalid case if custom_date selected but no date given
                      start_dt, end_dt = None, None
            # 'custom_range' is handled by start_dt and end_dt being set directly

        return (start_dt, end_dt)
    except Exception as e:
        current_app.logger.error(f"Error parsing date range: {e}", exc_info=True)
        return (None, None)


def send_push_notification(user_id, title, body, link):
    """Sends a push notification to a specific user's registered devices."""
    # Use Flask logger instead of print
    current_app.logger.info(f"DEBUG PUSH: Entered send_push_notification function for user_id: {user_id}")
    app = current_app._get_current_object() # Get the actual app instance for the background thread
    with app.app_context(): # Need app context to access config and DB
        user = User.query.get(user_id)
        if not user:
            current_app.logger.warning(f"DEBUG PUSH: User with id {user_id} not found. Exiting function.")
            return

        subscriptions = PushSubscription.query.filter_by(user_id=user.id).all()
        if not subscriptions:
            current_app.logger.info(f"DEBUG PUSH: No push subscriptions found for user {user.name}. Exiting function.")
            return

        current_app.logger.info(f"DEBUG PUSH: Found {len(subscriptions)} subscriptions for user {user.name}.")

        # Retrieve VAPID keys and claim email from config
        vapid_private_key = app.config.get('VAPID_PRIVATE_KEY')
        vapid_claims = {"sub": f"mailto:{app.config.get('VAPID_CLAIM_EMAIL', '')}"}

        if not vapid_private_key:
            current_app.logger.error('DEBUG PUSH: VAPID_PRIVATE_KEY is not configured. Cannot send push notifications.')
            return

        # Iterate through each subscription and attempt to send the notification
        for sub in subscriptions:
            try:
                try:
                    # Parse the JSON subscription info stored in the database
                    sub_json = json.loads(sub.subscription_json)
                except Exception as parse_ex:
                    current_app.logger.error(f"DEBUG PUSH: Could not parse subscription JSON for PushSubscription id={sub.id}: {parse_ex}")
                    current_app.logger.debug(f"DEBUG PUSH: Raw subscription_json: {sub.subscription_json}")
                    continue # Skip this invalid subscription

                # Log part of the endpoint for debugging identification
                endpoint = sub_json.get('endpoint', '')[:80] # Truncate long endpoints
                current_app.logger.info(f"DEBUG PUSH: Sending to subscription endpoint starting with: {endpoint}... (subscription id={sub.id})")

                # Send the push notification using pywebpush
                webpush(
                    subscription_info=sub_json,
                    data=json.dumps({'title': title, 'body': body, 'link': link}), # Payload must be JSON string
                    vapid_private_key=vapid_private_key,
                    vapid_claims=vapid_claims
                )

                current_app.logger.info(f"DEBUG PUSH: Successfully sent push notification to endpoint starting with {endpoint}.")
            except WebPushException as ex:
                # Handle common push exceptions (like expired subscriptions)
                current_app.logger.error(f"DEBUG PUSH: Web push failed for endpoint starting with {endpoint}. Exception: {ex}")
                # Log response details if available
                if hasattr(ex, 'response') and ex.response:
                    current_app.logger.error(f"DEBUG PUSH: WebPushException status code: {ex.response.status_code}, body: {ex.response.text}")
                # Consider deleting expired subscriptions (e.g., if status code is 404 or 410)
                # if ex.response and ex.response.status_code in [404, 410]:
                #     db.session.delete(sub)
                #     db.session.commit()
                #     current_app.logger.info(f"DEBUG PUSH: Deleted expired subscription id={sub.id}")
            except Exception as e:
                # Catch unexpected errors during the webpush call
                current_app.logger.error(f"DEBUG PUSH: An unexpected error occurred sending to endpoint starting with {endpoint}: {e}", exc_info=True)


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
    # Redirect users based on role after login
    if current_user.role in ['Requester', 'Property Manager']:
        return redirect(url_for('main.my_requests'))
    else: # Admin, Scheduler, Super User
        return redirect(url_for('main.dashboard'))

@main.route('/dashboard')
@login_required
def dashboard():
    # Only allow Admin, Scheduler, Super User access to dashboard
    if current_user.role in ['Requester', 'Property Manager']:
        return redirect(url_for('main.my_requests'))

    # Define all possible statuses for the dashboard cards
    all_statuses = [
        'New', 'Open', 'Pending', 'Quote Requested', 'Quote Sent', 'Approved',
        'Quote Declined', 'Scheduled', 'Completed', 'Closed', 'Cancelled'
    ]

    # Base query for non-deleted work orders
    base_query = WorkOrder.query.filter_by(is_deleted=False)

    # Get counts for each status efficiently
    status_counts_query = base_query.with_entities(WorkOrder.status, func.count(WorkOrder.id)).group_by(WorkOrder.status).all()
    db_counts = {status: count for status, count in status_counts_query}

    # Prepare stats dictionary, ensuring all defined statuses have a count (even if 0)
    stats = {status: db_counts.get(status, 0) for status in all_statuses}
    stats['totalRequests'] = sum(db_counts.values())

    # Get all work orders for tag and chart calculations (consider performance for large datasets)
    all_work_orders = base_query.all() # Fetch all needed for detailed counters

    # Calculate tag counts
    all_tags = []
    for wo in all_work_orders:
        if wo.tag:
            all_tags.extend(filter(None, wo.tag.split(','))) # Filter empty strings from split
    tag_counts = Counter(all_tags)

    # Prepare specific tag stats for display
    tag_stats = {
        "approved": tag_counts.get('Approved', 0),
        "declined": tag_counts.get('Declined', 0),
        "follow_up": tag_counts.get('Follow-up needed', 0),
        "go_back": tag_counts.get('Go-back', 0)
    }

    # Prepare data for charts
    status_counts_for_chart = Counter(req.status for req in all_work_orders)
    type_counts = Counter(req.request_type_relation.name for req in all_work_orders if req.request_type_relation)
    property_counts = Counter(req.property for req in all_work_orders)
    vendor_counts = Counter(req.vendor.company_name for req in all_work_orders if req.vendor)

    # Approvals/Declines by Property Manager
    approved_by_pm = Counter(wo.property_manager for wo in all_work_orders if wo.tag and 'Approved' in wo.tag.split(',') and wo.property_manager)
    declined_by_pm = Counter(wo.property_manager for wo in all_work_orders if wo.tag and 'Declined' in wo.tag.split(',') and wo.property_manager)

    # Go-backs by Vendor
    goback_work_orders = [wo for wo in all_work_orders if wo.tag and 'Go-back' in wo.tag.split(',')]
    goback_by_vendor = Counter(wo.vendor.company_name if wo.vendor else 'Unassigned' for wo in goback_work_orders)

    # Define colors for charts (ensure all statuses used in charts are included)
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
        'Approved': {'bg': 'bg-green-100', 'text': 'text-green-800', 'border': 'border-green-500', 'rgba': 'rgba(34, 197, 94, 0.8)'},
        'Quote Declined': {'bg': 'bg-red-100', 'text': 'text-red-800', 'border': 'border-red-500', 'rgba': 'rgba(239, 68, 68, 0.8)'},
    }
    tag_colors = {
        'Approved': {'bg': 'bg-green-100', 'text': 'text-green-800', 'border': 'border-green-500', 'rgba': 'rgba(34, 197, 94, 0.8)'},
        'Declined': {'bg': 'bg-red-100', 'text': 'text-red-800', 'border': 'border-red-500', 'rgba': 'rgba(239, 68, 68, 0.8)'},
        'Follow-up needed': {'bg': 'bg-purple-100', 'text': 'text-purple-800', 'border': 'border-purple-500', 'rgba': 'rgba(168, 85, 247, 0.8)'},
        'Go-back': {'bg': 'bg-blue-100', 'text': 'text-blue-800', 'border': 'border-blue-500', 'rgba': 'rgba(59, 130, 246, 0.8)'},
    }
    generic_chart_colors = [ # Reusable color palette
        'rgba(54, 162, 235, 0.8)', 'rgba(255, 206, 86, 0.8)',
        'rgba(75, 192, 192, 0.8)', 'rgba(153, 102, 255, 0.8)',
        'rgba(255, 99, 132, 0.8)', 'rgba(255, 159, 64, 0.8)',
        'rgba(128, 128, 128, 0.8)', 'rgba(0, 102, 204, 0.8)',
        'rgba(204, 0, 102, 0.8)', 'rgba(102, 204, 0, 0.8)'
    ] * 3 # Repeat palette if many items

    # Structure data specifically for Chart.js
    chart_data = {
        "status": {
            "labels": list(status_counts_for_chart.keys()),
            "data": list(status_counts_for_chart.values()),
            "colors": [status_colors.get(status, {}).get('rgba', 'rgba(156, 163, 175, 0.8)') for status in status_counts_for_chart.keys()]
        },
        "type": {
            "labels": list(type_counts.keys()),
            "data": list(type_counts.values()),
            "colors": generic_chart_colors[:len(type_counts)] # Use appropriate number of colors
        },
        "property": {
            "labels": list(property_counts.keys()),
            "data": list(property_counts.values()),
            "colors": generic_chart_colors[:len(property_counts)]
        },
        "vendor": {
            "labels": list(vendor_counts.keys()),
            "data": list(vendor_counts.values()),
            "colors": generic_chart_colors[:len(vendor_counts)]
        },
        "approved_by_pm": {
            "labels": list(approved_by_pm.keys()),
            "data": list(approved_by_pm.values()),
            "colors": [tag_colors['Approved']['rgba']] * len(approved_by_pm) # Use consistent color
        },
        "declined_by_pm": {
            "labels": list(declined_by_pm.keys()),
            "data": list(declined_by_pm.values()),
            "colors": [tag_colors['Declined']['rgba']] * len(declined_by_pm) # Use consistent color
        },
        "goback_by_vendor": {
            "labels": list(goback_by_vendor.keys()),
            "data": list(goback_by_vendor.values()),
            "colors": generic_chart_colors[:len(goback_by_vendor)]
        }
    }

    return render_template(
        'dashboard.html', title='Dashboard', stats=stats, all_statuses=all_statuses,
        tag_stats=tag_stats, chart_data=chart_data, status_colors=status_colors, tag_colors=tag_colors
    )

@main.route('/requests')
@login_required
@admin_required # Ensure only admins/schedulers/superusers can see all requests
def all_requests():
    requests_data = WorkOrder.query.filter_by(is_deleted=False).order_by(WorkOrder.date_created.desc()).all()
    # Convert work orders to dictionary format for JSON serialization
    requests_list = [work_order_to_dict(req) for req in requests_data]
    return render_template('all_requests.html', title='All Requests',
                           requests_json=json.dumps(requests_list)) # Pass as JSON for Alpine.js

@main.route('/my-requests')
@login_required
def my_requests():
    query = WorkOrder.query.filter_by(is_deleted=False)
    # Filter based on user role
    if current_user.role == 'Property Manager':
        # Property Managers see requests where they are assigned
        query = query.filter(WorkOrder.property_manager == current_user.name)
    elif current_user.role == 'Requester':
        # Requesters see requests they created
        query = query.filter_by(author=current_user)
    # Admin/Scheduler/SuperUser would typically use /requests or /dashboard,
    # but if they land here, this will show requests they authored (adjust if needed)
    elif current_user.role not in ['Admin', 'Scheduler', 'Super User']:
         # Fallback for unexpected roles? Or maybe just use the author filter.
         query = query.filter_by(author=current_user)


    user_requests = query.order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in user_requests]
    return render_template('my_requests.html', title='My Requests',
                           requests_json=json.dumps(requests_list))


@main.route('/shared-with-me')
@login_required
def shared_requests():
    # Show requests where the current user is listed in the 'viewers' relationship
    query = WorkOrder.query.filter_by(is_deleted=False).filter(WorkOrder.viewers.contains(current_user))
    requests_data = query.order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in requests_data]
    return render_template('shared_requests.html', title='Shared With Me',
                           requests_json=json.dumps(requests_list))

@main.route('/requests/status/<status>')
@login_required
@admin_required # Assuming only admins should filter all requests by status
def requests_by_status(status):
    # Filter all non-deleted requests by the given status
    filtered_requests = WorkOrder.query.filter_by(is_deleted=False, status=status).order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in filtered_requests]
    return render_template('requests_by_status.html', title=f'Requests: {status}',
                           requests_json=json.dumps(requests_list), status=status)

@main.route('/requests/tag/<tag_name>')
@login_required
@admin_required # Assuming only admins should filter all requests by tag
def requests_by_tag(tag_name):
    # Filter all non-deleted requests where the tag field contains the tag_name
    tagged_requests = WorkOrder.query.filter_by(is_deleted=False).filter(WorkOrder.tag.like(f'%{tag_name}%')).order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in tagged_requests]
    return render_template('requests_by_tag.html', title=f'Requests Tagged: {tag_name}',
                           requests_json=json.dumps(requests_list), tag_name=tag_name)

# --- VIEW REQUEST (MAIN DETAIL PAGE) ---
@main.route('/request/<int:request_id>', methods=['GET'])
@login_required
def view_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)

    # Permission checks
    if work_order.is_deleted and current_user.role != 'Super User':
        abort(404) # Hide deleted requests unless Super User

    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']

    # User must meet at least one condition to view
    if not (is_author or is_viewer or is_property_manager or is_admin_staff):
        abort(403) # Forbidden access

    # Auto-update status from 'New' to 'Open' if viewed by admin staff
    if not work_order.is_deleted and is_admin_staff and work_order.status == 'New':
        work_order.status = 'Open'
        db.session.add(AuditLog(text='Status changed to Open upon first view by admin staff.', user_id=current_user.id, work_order_id=work_order.id))
        flash('Request status automatically updated to Open.', 'info')
        # Commit immediately or let the view log commit handle it? Commit here for clarity.
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error committing auto status change: {e}")
            flash('Error automatically updating status.', 'danger')

    # Log the viewing action (only if allowed to view)
    db.session.add(AuditLog(text='Viewed the request.', user_id=current_user.id, work_order_id=work_order.id))
    try:
        db.session.commit() # Commit the view log (and potentially the status change)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error committing view log: {e}")

    # Fetch related data for the template
    notes = Note.query.filter_by(work_order_id=request_id).order_by(Note.date_posted.asc()).all()
    audit_logs = AuditLog.query.filter_by(work_order_id=request_id).order_by(AuditLog.timestamp.desc()).all()
    quotes = work_order.quotes # Fetch quotes relationship (it's already a list or lazy loadable)
    all_users = User.query.filter_by(is_active=True).all() # For CC options etc.

    # Instantiate forms needed on the page
    note_form = NoteForm()
    status_form = ChangeStatusForm()
    attachment_form = AttachmentForm()
    assign_vendor_form = AssignVendorForm()
    quote_form = QuoteForm()
    delete_form = DeleteRestoreRequestForm() # For CSRF on delete/restore/remove tag/quote delete
    tag_form = TagForm() # Instantiate the form for follow-up date and CSRF
    reassign_form = ReassignRequestForm()
    follow_up_form = SendFollowUpForm()
    completed_form = MarkAsCompletedForm()
    go_back_form = GoBackForm() # For the Go-back toggle CSRF

    # Get initials for avatar placeholder
    requester_initials = get_requester_initials(work_order.requester_name)


    # --- REMOVED Block for setting tag_form.tag.choices ---
    # Since tag_form no longer has a 'tag' SelectField, this block is removed.
    # --- END REMOVAL ---


    return render_template('view_request.html', title=f'Request #{work_order.id}', work_order=work_order, notes=notes,
                           note_form=note_form, status_form=status_form, audit_logs=audit_logs,
                           attachment_form=attachment_form, assign_vendor_form=assign_vendor_form,
                           requester_initials=requester_initials, quote_form=quote_form, quotes=quotes,
                           delete_form=delete_form, tag_form=tag_form, reassign_form=reassign_form,
                           follow_up_form=follow_up_form, all_users=all_users, completed_form=completed_form,
                           go_back_form=go_back_form
                           )


# --- POST NOTE ---
@main.route('/request/<int:request_id>/post_note', methods=['POST'])
@login_required
def post_note(request_id):
    current_app.logger.info(f"--- ENTERED post_note route for request {request_id} ---")
    work_order = WorkOrder.query.get_or_404(request_id)

    # Permission checks (same logic as view_request)
    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']
    if not (is_author or is_viewer or is_admin_staff or is_property_manager):
         current_app.logger.warning(f"Note POST permission denied for user {current_user.id} ({current_user.name}) on request {request_id}")
         return jsonify({'success': False, 'message': 'Permission denied.'}), 403

    note_form = NoteForm()
    current_app.logger.debug(f"Note POST raw form data: {request.form}")

    if note_form.validate_on_submit():
        current_app.logger.info("Note form validated successfully.")
        try:
            note_text = note_form.text.data
            # Note timestamp defaults to Denver time via model
            note = Note(text=note_text, author=current_user, work_order=work_order)
            db.session.add(note)
            current_app.logger.info("Note object created and added to session.")

            # Identify users to notify (author + mentioned users, excluding self)
            notified_users = set()
            if work_order.author and work_order.author != current_user:
                 notified_users.add(work_order.author)

            # Find mentions and add mentioned users to viewers if not already present
            tagged_names = re.findall(r'@(\w+(?:\s\w+)?)', note_text)
            current_app.logger.info(f"Found mentions: {tagged_names}")
            for name in tagged_names:
                search_name = name.strip()
                # Case-insensitive user lookup
                tagged_user = User.query.filter(func.lower(User.name) == func.lower(search_name)).first()
                if tagged_user:
                    current_app.logger.info(f"Found tagged user: {tagged_user.name} (ID: {tagged_user.id})")
                    if tagged_user not in work_order.viewers:
                        work_order.viewers.append(tagged_user)
                        current_app.logger.info(f"Added {tagged_user.name} to work_order viewers.")
                    if tagged_user != current_user:
                        notified_users.add(tagged_user)
                else:
                    current_app.logger.warning(f"Could not find user for mention: @{search_name}")

            # Commit note and viewer changes first to get note ID and ensure viewers are saved
            current_app.logger.info("Committing note and viewer changes...")
            db.session.commit()
            current_app.logger.info("Commit successful.")

            # Broadcast the new note via Socket.IO to the room for this request
            current_app.logger.info("Broadcasting note via Socket.IO...")
            broadcast_new_note(work_order.id, note)
            current_app.logger.info("Broadcast complete.")

            # Send email and push notifications to the identified users
            current_app.logger.info(f"Users to notify via Push/Email: {[user.name for user in notified_users]}")
            for user in notified_users:
                current_app.logger.info(f"Processing PUSH/EMAIL for user: {user.name} (ID: {user.id})")
                notification_text = f'{current_user.name} mentioned you in a note on Request #{work_order.id}'
                notification_link = url_for('main.view_request', request_id=work_order.id, _external=True)

                # Create DB notification (timestamp defaults to Denver time)
                notification = Notification(
                    text=notification_text,
                    link=url_for('main.view_request', request_id=work_order.id), # Internal link for DB
                    user_id=user.id
                )
                db.session.add(notification)
                current_app.logger.info(f"Added Notification object for user {user.id} to session.")

                # Send Push Notification
                current_app.logger.info(f"DEBUG PUSH (Pre-call): Preparing push for user {user.id} ({user.name})")
                send_push_notification(user.id, 'New Mention', notification_text, notification_link)
                current_app.logger.info(f"DEBUG PUSH (Post-call): Returned from send_push_notification for user {user.id}")

                # Send Email Notification
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
                        link=notification_link # Use external link for email
                    )
                )
                current_app.logger.info(f"DEBUG EMAIL (Post-call): Returned from send_notification_email for user {user.id}")

            current_app.logger.info("Committing notifications...")
            db.session.commit() # Commit notifications
            current_app.logger.info("Notifications commit successful. Returning success JSON.")
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback() # Rollback all changes from this request on error
            current_app.logger.error(f"Error posting note for request {request_id}: {e}", exc_info=True)
            return jsonify({'success': False, 'message': 'An internal server error occurred.'}), 500
    else:
        # Form validation failed
        current_app.logger.warning(f"Note form validation FAILED for request {request_id}. Errors: {note_form.errors}")
        # Return validation errors to the client
        return jsonify({'success': False, 'errors': note_form.errors}), 400


# --- MARK AS COMPLETED ---
@main.route('/request/<int:request_id>/mark_as_completed', methods=['POST'])
@login_required
def mark_as_completed(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    # Permissions: Author, assigned PM, or viewer can mark complete
    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    if not (is_author or is_viewer or is_property_manager):
         flash('You do not have permission to mark this request as completed.', 'danger')
         return redirect(url_for('main.view_request', request_id=request_id))

    form = MarkAsCompletedForm()
    if form.validate_on_submit():
        if work_order.status not in ['Closed', 'Cancelled', 'Completed']:
            old_status = work_order.status
            work_order.status = 'Completed'
            work_order.date_completed = get_denver_now() # Set completion time
            db.session.add(AuditLog(text=f'Request marked as completed (Status changed from {old_status}).', user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash('Request has been marked as completed.', 'success')
        else:
            flash(f'Request status is already {work_order.status}.', 'info')
    else:
        flash('There was an error marking the request as completed (CSRF validation failed).', 'danger')
    return redirect(url_for('main.view_request', request_id=request_id))

# --- SEND MANUAL FOLLOW-UP EMAIL ---
@main.route('/request/<int:request_id>/send_follow_up', methods=['POST'])
@login_required
@admin_required # Only Admin/Scheduler/SuperUser can send manual follow-ups
def send_follow_up(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = SendFollowUpForm()
    if form.validate_on_submit():
        recipient = form.recipient.data
        cc = form.cc.data
        subject = form.subject.data
        body = form.body.data

        recipients = [recipient]
        cc_list = [email.strip() for email in cc.split(',') if email.strip()] if cc else []

        # Convert plain text body to simple HTML <p> tags with line breaks
        html_body = f"<p>{body.replace(chr(10), '<br>')}</p>"

        try:
            send_notification_email(
                subject=subject,
                recipients=recipients,
                cc=cc_list,
                html_body=html_body,
                text_body=body # Use original plain text for text part
            )
            # AuditLog timestamp defaults to Denver time
            db.session.add(AuditLog(text=f"Manual follow-up email sent to {recipient}", user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash('Follow-up email sent successfully!', 'success')
        except Exception as e:
            current_app.logger.error(f"Error sending manual follow-up email: {e}", exc_info=True)
            flash('Failed to send follow-up email due to a server error.', 'danger')

    else:
        flash('Failed to send follow-up email. Please check the form errors.', 'danger')
        # Log validation errors
        current_app.logger.warning(f"SendFollowUpForm validation failed: {form.errors}")

    return redirect(url_for('main.view_request', request_id=request_id))


# --- SOFT DELETE REQUEST ---
@main.route('/request/<int:request_id>/delete', methods=['POST'])
@login_required
@role_required(['Super User']) # Only Super Users can soft-delete
def delete_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = DeleteRestoreRequestForm() # For CSRF
    if form.validate_on_submit():
        if not work_order.is_deleted:
            work_order.is_deleted = True
            work_order.deleted_at = get_denver_now() # Record deletion time
            # AuditLog timestamp defaults to Denver time
            db.session.add(AuditLog(text='Request soft-deleted.', user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash(f'Request #{work_order.id} has been moved to the deleted items list. It can be restored.', 'success')
        else:
             flash(f'Request #{work_order.id} is already deleted.', 'info')
        # Redirect to dashboard or deleted items list after deletion
        return redirect(url_for('main.dashboard'))
    else:
        flash('Invalid request to delete the work order (CSRF validation failed).', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

# --- RESTORE SOFT-DELETED REQUEST ---
@main.route('/request/<int:request_id>/restore', methods=['POST'])
@login_required
@role_required(['Super User']) # Only Super Users can restore
def restore_request(request_id):
    # Query specifically for deleted items or use query.get if filter_by(is_deleted=True) is applied elsewhere
    work_order = WorkOrder.query.filter_by(id=request_id, is_deleted=True).first_or_404()
    form = DeleteRestoreRequestForm() # For CSRF
    if form.validate_on_submit():
        work_order.is_deleted = False
        work_order.deleted_at = None # Clear deletion time
        # AuditLog timestamp defaults to Denver time
        db.session.add(AuditLog(text='Request restored from deleted items.', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash(f'Request #{work_order.id} has been restored.', 'success')
        # Redirect back to the now-active request view
        return redirect(url_for('main.view_request', request_id=request_id))
    else:
        flash('Invalid request to restore the work order (CSRF validation failed).', 'danger')
        # Redirect back to deleted items list or the specific (still deleted) view if accessible
        return redirect(url_for('main.deleted_requests'))

# --- PERMANENTLY DELETE REQUEST ---
@main.route('/request/<int:request_id>/permanently-delete', methods=['POST'])
@login_required
@role_required(['Super User']) # Only Super Users can permanently delete
def permanently_delete_request(request_id):
    # Query specifically for deleted items to ensure it was soft-deleted first
    work_order = WorkOrder.query.filter_by(id=request_id, is_deleted=True).first_or_404()
    form = DeleteRestoreRequestForm() # For CSRF
    if form.validate_on_submit():
        try:
            # Manually delete related attachments first if cascade delete isn't reliable
            attachments = Attachment.query.filter_by(work_order_id=work_order.id).all()
            for attachment in attachments:
                 file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment.filename)
                 try:
                     if os.path.exists(file_path):
                         os.remove(file_path)
                 except OSError as e:
                     current_app.logger.error(f"Error removing attachment file during permanent delete: {file_path}, {e}")
                 db.session.delete(attachment)

            # Cascade delete should handle Notes, AuditLogs, Quotes, Message relations, viewers association
            db.session.delete(work_order)
            db.session.commit()
            flash(f'Request #{request_id} has been permanently deleted.', 'success')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error permanently deleting request {request_id}: {e}", exc_info=True)
            flash('An error occurred during permanent deletion.', 'danger')
        # Redirect to the list of deleted requests after deletion
        return redirect(url_for('main.deleted_requests'))
    else:
        flash('Invalid request to permanently delete the work order (CSRF validation failed).', 'danger')
        return redirect(url_for('main.deleted_requests'))

# --- VIEW DELETED REQUESTS LIST ---
@main.route('/deleted-requests')
@login_required
@role_required(['Super User']) # Only Super Users can view this list
def deleted_requests():
    deleted = WorkOrder.query.filter_by(is_deleted=True).order_by(WorkOrder.deleted_at.desc()).all()
    form = DeleteRestoreRequestForm() # For CSRF protection on restore/perm-delete buttons
    return render_template('deleted_requests.html', title='Deleted Requests', requests=deleted, form=form)


# --- CHANGE STATUS ---
@main.route('/change_status/<int:request_id>', methods=['POST'])
@login_required
@admin_required # Only Admin/Scheduler/SuperUser can change status via this form
def change_status(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = ChangeStatusForm()
    # Define base choices available in the dropdown (exclude 'New')
    base_choices = [('Open','Open'), ('Pending','Pending'), ('Quote Requested','Quote Requested'),
                    ('Quote Sent','Quote Sent'), ('Scheduled','Scheduled'), ('Completed','Completed'),
                    ('Closed','Closed'), ('Cancelled','Cancelled')]
    # Also include PM-specific statuses if they exist and might be set, but don't let admins select them here
    if work_order.status == 'Approved': base_choices.append(('Approved', 'Approved'))
    if work_order.status == 'Quote Declined': base_choices.append(('Quote Declined', 'Quote Declined'))

    # Filter choices shown in the dropdown (Admin/Scheduler/SuperUser cannot select PM-specific ones)
    form.status.choices = [c for c in base_choices if c[0] not in ['Approved', 'Quote Declined']]

    # Set default value in form to current status
    if request.method == 'GET': # Should normally not be GET, but handle defensively
         form.status.data = work_order.status
    elif request.method == 'POST' and not form.is_submitted(): # Pre-populate on initial POST load if needed
         form.status.data = work_order.status

    # Fetch context needed for re-rendering template in case of validation error
    notes = Note.query.filter_by(work_order_id=request_id).order_by(Note.date_posted.asc()).all()
    audit_logs = AuditLog.query.filter_by(work_order_id=request_id).order_by(AuditLog.timestamp.desc()).all()
    quotes = work_order.quotes
    all_users = User.query.filter_by(is_active=True).all()
    template_context = {
        'title': f'Request #{work_order.id}', 'work_order': work_order, 'notes': notes,
        'note_form': NoteForm(), 'status_form': form, 'audit_logs': audit_logs, # Pass back the current form instance
        'attachment_form': AttachmentForm(), 'assign_vendor_form': AssignVendorForm(),
        'requester_initials': get_requester_initials(work_order.requester_name), 'quote_form': QuoteForm(), 'quotes': quotes,
        'delete_form': DeleteRestoreRequestForm(), 'tag_form': TagForm(), 'reassign_form': ReassignRequestForm(), # Instantiate other forms needed
        'follow_up_form': SendFollowUpForm(), 'all_users': all_users, 'completed_form': MarkAsCompletedForm(),
        'go_back_form': GoBackForm()
    }


    if form.validate_on_submit():
        new_status = form.status.data

        # Validate Scheduled status requirements
        if new_status == 'Scheduled':
            if not work_order.vendor_id:
                flash('A vendor must be assigned before scheduling.', 'danger')
                return redirect(url_for('main.view_request', request_id=request_id))
            if not form.scheduled_date.data:
                flash('A scheduled date is required to change the status to "Scheduled".', 'danger')
                # Re-render with error
                form.status.data = work_order.status # Reset dropdown to current
                return render_template('view_request.html', **template_context)
            try:
                # Ensure date format is valid before proceeding
                new_scheduled_date_obj = datetime.strptime(form.scheduled_date.data, '%m/%d/%Y').date()
            except ValueError:
                 flash('Invalid date format for scheduled date (MM/DD/YYYY).', 'danger')
                 # Re-render with error
                 form.status.data = work_order.status # Reset dropdown
                 return render_template('view_request.html', **template_context)


        old_status = work_order.status
        # Handle case where only scheduled date changes, not status itself
        if old_status == new_status and new_status == 'Scheduled':
             new_scheduled_date = datetime.strptime(form.scheduled_date.data, '%m/%d/%Y').date()
             if work_order.scheduled_date != new_scheduled_date:
                 work_order.scheduled_date = new_scheduled_date
                 log_text = f'Scheduled date updated to {work_order.scheduled_date.strftime("%m/%d/%Y")}.'
                 db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
                 db.session.commit()
                 flash('Scheduled date updated.', 'success')
             else:
                 flash('No changes detected.', 'info') # No status or date change
             return redirect(url_for('main.view_request', request_id=request_id))
        elif old_status == new_status:
             flash('Status is already set to the selected value.', 'info') # No status change needed
             return redirect(url_for('main.view_request', request_id=request_id))


        # --- Process Status Change ---
        work_order.status = new_status
        log_text = f'Changed status from {old_status} to {new_status}.'

        # Handle Scheduled Date
        if new_status == 'Scheduled':
            # We already validated the date format above
            work_order.scheduled_date = datetime.strptime(form.scheduled_date.data, '%m/%d/%Y').date()
            log_text += f' for {work_order.scheduled_date.strftime("%m/%d/%Y")}'
        else:
            work_order.scheduled_date = None # Clear date if status is not Scheduled

        # Handle 'Completed' Tag and Date
        current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])
        if new_status in ['Closed', 'Completed']:
            current_tags.add('Completed')
            if not work_order.date_completed: # Only set if not already set
                 work_order.date_completed = get_denver_now() # Set completion time
            log_text += " Tagged as 'Completed'."
        elif old_status in ['Closed', 'Completed']: # If moving away from Closed/Completed
            current_tags.discard('Completed')
            # work_order.date_completed = None # Decide if date should be cleared

        work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
        db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))

        # --- Send Notifications ---
        # Notify Author
        if work_order.author and work_order.author != current_user:
            notification_text = f'Status for Request #{work_order.id} changed to {new_status}.'
            notification_link_internal = url_for('main.view_request', request_id=work_order.id)
            notification_link_external = url_for('main.view_request', request_id=work_order.id, _external=True)

            notification = Notification(text=notification_text, link=notification_link_internal, user_id=work_order.user_id)
            db.session.add(notification)
            send_push_notification(work_order.user_id, 'Request Status Updated', notification_text, notification_link_external)

            email_body = f"<p>The status of your Request #{work_order.id} for property <b>{work_order.property}</b> was changed from <b>{old_status}</b> to <b>{new_status}</b>.</p>"
            send_notification_email(
                subject=f"Status Update for Request #{work_order.id}", recipients=[work_order.author.email],
                text_body=notification_text,
                html_body=render_template('email/notification_email.html', title="Request Status Updated", user=work_order.author, body_content=email_body, link=notification_link_external)
            )

        # Notify PM if Quote Sent
        if new_status == 'Quote Sent' and work_order.property_manager:
            manager = User.query.filter_by(name=work_order.property_manager, role='Property Manager').first()
            if manager and manager != current_user: # Don't notify PM if they made the change
                notification_text_pm = f'A quote has been sent for Request #{work_order.id} at {work_order.property}.'
                notification_link_internal_pm = url_for('main.view_request', request_id=work_order.id)
                notification_link_external_pm = url_for('main.view_request', request_id=work_order.id, _external=True)

                manager_notification = Notification(text=notification_text_pm, link=notification_link_internal_pm, user_id=manager.id)
                db.session.add(manager_notification)
                send_push_notification(manager.id, 'Quote Approval Needed', notification_text_pm, notification_link_external_pm)

                email_body_pm = f"<p>A quote has been sent and requires your approval for Request #{work_order.id} at property <b>{work_order.property}</b>.</p>"
                send_notification_email(
                    subject=f"Quote Approval Needed for Request #{work_order.id}", recipients=[manager.email],
                    text_body=f"A quote requires your approval for Request #{work_order.id}.",
                    html_body=render_template('email/notification_email.html', title="Quote Approval Needed", user=manager, body_content=email_body_pm, link=notification_link_external_pm)
                )

        db.session.commit()
        flash(f'Status updated to {new_status}.', 'success')
    else:
        # Log form errors for debugging
        current_app.logger.warning(f"ChangeStatusForm validation failed: {form.errors}")
        # Flash specific errors (already handled by WTForms in template generally)
        flash('Could not update status. Please check errors below.', 'danger')
        # Re-render the template with errors
        return render_template('view_request.html', **template_context)


    return redirect(url_for('main.view_request', request_id=request_id))


# --- QUOTE ACTIONS (APPROVE/DECLINE/CLEAR) ---
@main.route('/request/<int:request_id>/quote/<int:quote_id>/<action>', methods=['POST'])
@login_required
def quote_action(request_id, quote_id, action):
    work_order = WorkOrder.query.get_or_404(request_id)
    quote = Quote.query.get_or_404(quote_id)
    # Ensure quote belongs to the work order
    if quote.work_order_id != work_order.id:
        abort(404)

    # Permissions: PM or Super User can approve/decline
    can_pm = current_user.role == 'Property Manager' and current_user.name == work_order.property_manager
    can_super_user = current_user.role == 'Super User'

    if not (can_pm or can_super_user):
        if request.accept_mimetypes.accept_json:
            return jsonify({'success': False, 'error': 'permission', 'message': 'You do not have permission to approve or decline quotes.'}), 403
        flash('You do not have permission to approve or decline quotes.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

    current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])
    log_text = "" # Initialize log text
    flash_text = "" # Initialize flash message

    current_app.logger.info(f"Quote action requested: action={action}, quote_id={quote_id}, current_status={quote.status}")

    if action == 'approve':
        if quote.status == 'Approved':
            if request.accept_mimetypes.accept_json:
                return jsonify({'success': False, 'error': 'already', 'message': 'Quote already approved.'}), 400
            flash(f"Quote from {quote.vendor.company_name} is already approved.", 'info')
            return redirect(url_for('main.view_request', request_id=request_id))

        # Mark this quote approved
        quote.status = 'Approved'
        work_order.approved_quote_id = quote.id  # Link the approved quote
        current_tags.add('Approved')
        current_tags.discard('Declined')
        log_text = f"Quote from {quote.vendor.company_name} approved."
        flash_text = f"Quote from {quote.vendor.company_name} has been approved."

        # Update Work Order status if appropriate
        if work_order.status == 'Quote Sent':
            work_order.status = 'Approved'  # Status indicating quote is approved
            log_text += f" Work Order status changed to {work_order.status}."

    elif action == 'decline':
        if quote.status == 'Declined':
            if request.accept_mimetypes.accept_json:
                return jsonify({'success': False, 'error': 'already', 'message': 'Quote already declined.'}), 400
            flash(f"Quote from {quote.vendor.company_name} is already declined.", 'info')
            return redirect(url_for('main.view_request', request_id=request_id))

        quote.status = 'Declined'
        # If this was the currently approved quote, clear the link
        if work_order.approved_quote_id == quote.id:
            work_order.approved_quote_id = None

        # Check if ANY other quote is still approved
        other_quotes_approved = Quote.query.filter(
            Quote.work_order_id == work_order.id,
            Quote.id != quote.id,  # Exclude the one just declined
            Quote.status == 'Approved'
        ).count() > 0

        if not other_quotes_approved:
            current_tags.discard('Approved')  # Remove 'Approved' if none are left
            # Add 'Declined' only if no others are approved
            current_tags.add('Declined')
        else:
            # If another is approved, ensure 'Declined' tag is NOT added
            current_tags.discard('Declined')


        log_text = f"Quote from {quote.vendor.company_name} declined."
        flash_text = f"Quote from {quote.vendor.company_name} has been declined."

        # Update Work Order status if needed (e.g., if no quotes are approved anymore)
        if work_order.status in ['Quote Sent', 'Approved'] and not other_quotes_approved:
             work_order.status = 'Quote Declined'
             log_text += f" Work Order status changed to {work_order.status}."


    elif action == 'clear':
        # If there's no status set, nothing to clear
        if not quote.status: # Checks if status is None or empty string
            if request.accept_mimetypes.accept_json:
                return jsonify({'success': False, 'error': 'already', 'message': 'Quote status already cleared.'}), 400
            flash(f"Status for quote from {quote.vendor.company_name} is already cleared.", 'info')
            return redirect(url_for('main.view_request', request_id=request_id))

        original_status = quote.status # Store original for logging/comparison if needed
        quote.status = None # Set status to None (will now work with model change)

        # If this was the approved quote, clear the link
        if work_order.approved_quote_id == quote.id:
            work_order.approved_quote_id = None

        # Re-evaluate tags based on remaining quotes' statuses
        other_quotes_approved = Quote.query.filter(Quote.work_order_id == work_order.id, Quote.id != quote.id, Quote.status == 'Approved').count() > 0
        other_quotes_declined = Quote.query.filter(Quote.work_order_id == work_order.id, Quote.id != quote.id, Quote.status == 'Declined').count() > 0

        if not other_quotes_approved:
            current_tags.discard('Approved')
        # Remove 'Declined' tag only if NO other quotes are declined
        if not other_quotes_declined:
            current_tags.discard('Declined')

        log_text = f"Status '{original_status}' for quote from {quote.vendor.company_name} cleared."
        flash_text = f"Status for quote from {quote.vendor.company_name} has been cleared."

        # Reset Work Order status if it was Approved/Declined and now no quotes are in those states
        if work_order.status in ['Approved', 'Quote Declined'] and not other_quotes_approved and not other_quotes_declined:
            # Check if any quotes are still in non-cleared states (e.g., 'Quote Sent')
            any_active = Quote.query.filter(Quote.work_order_id == work_order.id, Quote.status.isnot(None)).count() > 0
            if any_active:
                work_order.status = 'Quote Sent' # Revert to Quote Sent if others exist
            else:
                work_order.status = 'Open' # Or maybe 'Quote Requested' if appropriate? Defaulting to Open
            log_text += f" Work Order status reset to {work_order.status}."
        elif work_order.status == 'Quote Declined' and other_quotes_approved: # If we clear a declined quote but another is approved
            work_order.status = 'Approved'
            log_text += f" Work Order status reset to {work_order.status}."


    else: # Invalid action
        flash('Invalid action specified.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

    # Update tag string and commit changes
    work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
    db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))

    try: # Wrap commit in try/except for robustness
        db.session.commit()
        flash(flash_text, 'success')
        if request.accept_mimetypes.accept_json:
            # Return None for new_status when cleared
            return jsonify({'success': True, 'quote_id': quote.id, 'new_status': quote.status, 'tags': work_order.tag, 'message': flash_text})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error committing quote action ({action}) for quote {quote_id}: {e}", exc_info=True)
        flash('An error occurred while updating the quote status.', 'danger')
        if request.accept_mimetypes.accept_json:
            return jsonify({'success': False, 'message': 'Database error during commit.'}), 500

    # Fallback redirect for non-AJAX or errors during AJAX commit
    return redirect(url_for('main.view_request', request_id=request_id))


# --- TOGGLE GO-BACK TAG ---
@main.route('/request/<int:request_id>/toggle_goback', methods=['POST'])
@login_required
def toggle_go_back(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = GoBackForm() # Use for CSRF protection

    if form.validate_on_submit():
        current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])
        tag_name = 'Go-back'
        log_text = ""
        flash_text = ""

        if tag_name in current_tags:
            current_tags.remove(tag_name)
            log_text = f"Tag '{tag_name}' removed."
            flash_text = f"Tag '{tag_name}' has been removed."
        else:
            current_tags.add(tag_name)
            log_text = f"Request tagged as '{tag_name}'."
            flash_text = f"Request has been tagged as '{tag_name}'."

        work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
        db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        #flash(flash_text, 'success') # Flash message might be redundant if UI updates instantly

        # Return JSON for AJAX update
        if request.accept_mimetypes.accept_json:
            # Render the updated tags partial to send back
            rendered_tags_html = render_template('partials/_tags_display.html', work_order=work_order, delete_form=DeleteRestoreRequestForm()) # You might need to pass delete_form for remove buttons
            return jsonify({'success': True, 'tags': rendered_tags_html, 'action': 'toggled', 'tag': tag_name})
    else:
        # Handle CSRF failure
        csrf_error = False
        if hasattr(form, 'csrf_token') and form.csrf_token.errors:
             csrf_error = True
             flash_text = 'CSRF validation failed. Please try again.'
        else:
             flash_text = 'Invalid request to toggle Go-back tag.'

        flash(flash_text, 'danger')
        if request.accept_mimetypes.accept_json:
            return jsonify({'success': False, 'message': flash_text}), 400 # Return error for AJAX

    # Fallback redirect
    return redirect(url_for('main.view_request', request_id=request_id))


# --- ADD/REMOVE OTHER TAGS (like Follow-up) ---
@main.route('/tag_request/<int:request_id>', methods=['POST'])
@login_required
def tag_request(request_id):
    # Log entry into the function
    current_app.logger.info(f"Entered tag_request route for request_id: {request_id}")
    current_app.logger.debug(f"Request Form Data: {request.form}")

    work_order = WorkOrder.query.get_or_404(request_id)
    # *** FIX: Instantiate form with request data for validation ***
    form = TagForm(request.form)
    # *** END FIX ***

    # Permissions
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']

    action = request.form.get('action')
    # Determine tag name based on action
    tag_name = None
    if action == 'add_tag':
        tag_name = request.form.get('tag_to_add')
    elif action == 'remove_tag':
        tag_name = request.form.get('tag_to_remove')

    # Log action and tag name
    current_app.logger.debug(f"Action: {action}, Tag Name: {tag_name}")

    if tag_name != 'Follow-up needed':
        current_app.logger.warning(f"Invalid tag operation attempted: {tag_name}")
        flash('Invalid tag operation.', 'danger')
        if request.accept_mimetypes.accept_json:
             return jsonify({'success': False, 'message': 'Invalid tag operation.'}), 400
        return redirect(url_for('main.view_request', request_id=request_id))

    # --- Handle REMOVE action ---
    if action == 'remove_tag':
        current_app.logger.info(f"Processing remove_tag for '{tag_name}' on WO #{request_id}")
        if not is_admin_staff:
             current_app.logger.warning(f"Permission denied for user {current_user.id} to remove tag '{tag_name}' on WO #{request_id}")
             flash(f"You do not have permission to remove the '{tag_name}' tag.", 'danger')
             if request.accept_mimetypes.accept_json:
                 return jsonify({'success': False, 'message': 'Permission denied.'}), 403
             return redirect(url_for('main.view_request', request_id=request_id))

        # *** Use the form instance populated with request.form for validation ***
        if form.validate_on_submit():
            current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])
            if tag_name in current_tags:
                current_tags.remove(tag_name)
                log_text = f"Tag '{tag_name}' removed."
                flash_text = f"Tag '{tag_name}' has been removed."
                work_order.follow_up_date = None
                log_text += " Follow-up date cleared."

                work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
                db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
                try:
                    db.session.commit()
                    flash(flash_text, 'info')
                    if request.accept_mimetypes.accept_json:
                        rendered_tags_html = render_template('partials/_tags_display.html', work_order=work_order, delete_form=DeleteRestoreRequestForm())
                        return jsonify({'success': True, 'tags': rendered_tags_html, 'action': 'removed', 'tag': tag_name})
                except Exception as e:
                     db.session.rollback()
                     current_app.logger.error(f"Error committing tag removal: {e}", exc_info=True)
                     flash('Error saving changes.', 'danger')
                     if request.accept_mimetypes.accept_json:
                         return jsonify({'success': False, 'message': 'Database error.'}), 500
            else:
                 flash(f"Tag '{tag_name}' was not found.", 'warning')
                 if request.accept_mimetypes.accept_json:
                     rendered_tags_html = render_template('partials/_tags_display.html', work_order=work_order, delete_form=DeleteRestoreRequestForm())
                     return jsonify({'success': True, 'tags': rendered_tags_html, 'action': 'not_found', 'tag': tag_name})
        else:
            # *** Log CSRF specific error if possible ***
            csrf_error_msg = 'CSRF validation failed.' if 'csrf_token' in form.errors else 'Form validation failed.'
            current_app.logger.warning(f"{csrf_error_msg} for tag removal. Errors: {form.errors}")
            flash(f'Could not remove tag. {csrf_error_msg}', 'danger')
            if request.accept_mimetypes.accept_json:
                 return jsonify({'success': False, 'error': 'CSRF' if 'csrf_token' in form.errors else 'Validation'}), 400
        # Fallback redirect
        return redirect(url_for('main.view_request', request_id=request_id))

    # --- Handle ADD action ---
    elif action == 'add_tag':
        current_app.logger.info(f"Processing add_tag for '{tag_name}' on WO #{request_id}")
        if not is_admin_staff:
            current_app.logger.warning(f"Permission denied for user {current_user.id} to add tag '{tag_name}' on WO #{request_id}")
            flash(f"You do not have permission to add the '{tag_name}' tag.", 'danger')
            if request.accept_mimetypes.accept_json:
                 return jsonify({'success': False, 'message': 'Permission denied.'}), 403
            return redirect(url_for('main.view_request', request_id=request_id))

        # *** Use the form instance populated with request.form for validation ***
        if form.validate_on_submit():
            current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])

            # --- Date Validation is now handled by WTForms validator via form.validate_on_submit() ---
            follow_up_date_obj = form.follow_up_date.data # Get validated date data

            # Check if date is provided (it's Optional in form, but required if adding this tag)
            if not follow_up_date_obj:
                 flash('A follow-up date is required when adding the "Follow-up needed" tag.', 'danger')
                 if request.accept_mimetypes.accept_json:
                     return jsonify({'success': False, 'errors': {'follow_up_date': ['A valid MM/DD/YYYY date is required.']}}), 400
                 return redirect(url_for('main.view_request', request_id=request_id))
            # --- End Date Validation ---

            # Proceed if validation passed
            log_text = ""
            commit_needed = False
            tag_added = False

            if tag_name not in current_tags:
                 current_tags.add(tag_name)
                 log_text = f"Request tagged as '{tag_name}'."
                 commit_needed = True
                 tag_added = True

            # Use date object directly
            if work_order.follow_up_date != follow_up_date_obj:
                  work_order.follow_up_date = follow_up_date_obj
                  date_log = f" Date set to {follow_up_date_obj.strftime('%m/%d/%Y')}."
                  if commit_needed:
                       log_text += date_log
                  else:
                       log_text = f"Follow-up date updated to {follow_up_date_obj.strftime('%m/%d/%Y')}."
                  commit_needed = True

            if commit_needed:
                work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
                db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
                try:
                    db.session.commit()
                    flash_text = log_text if tag_added else "Follow-up date updated."
                    flash(flash_text, 'success')
                    if request.accept_mimetypes.accept_json:
                        rendered_tags_html = render_template('partials/_tags_display.html', work_order=work_order, delete_form=DeleteRestoreRequestForm())
                        return jsonify({'success': True, 'tags': rendered_tags_html, 'action': 'added', 'tag': tag_name, 'follow_up_date': work_order.follow_up_date.strftime('%m/%d/%Y') if work_order.follow_up_date else None})
                except Exception as e:
                     db.session.rollback()
                     current_app.logger.error(f"Error committing tag add/date update: {e}", exc_info=True)
                     flash('Error saving changes.', 'danger')
                     if request.accept_mimetypes.accept_json:
                          return jsonify({'success': False, 'message': 'Database error.'}), 500
            else:
                flash(f"Request is already tagged as '{tag_name}' with the specified date.", 'info')
                if request.accept_mimetypes.accept_json:
                    rendered_tags_html = render_template('partials/_tags_display.html', work_order=work_order, delete_form=DeleteRestoreRequestForm())
                    return jsonify({'success': True, 'tags': rendered_tags_html, 'action': 'no_change', 'tag': tag_name, 'follow_up_date': work_order.follow_up_date.strftime('%m/%d/%Y') if work_order.follow_up_date else None})


        else: # Form validation failed (could be CSRF or date format)
            # *** Log specific errors ***
            csrf_error_msg = 'CSRF validation failed.' if 'csrf_token' in form.errors else ''
            date_error_msg = form.errors.get('follow_up_date', [''])[0] if 'follow_up_date' in form.errors else ''
            error_msg = f"Error adding tag. {csrf_error_msg} {date_error_msg}".strip()
            current_app.logger.warning(f"{error_msg} Errors: {form.errors}")
            flash(error_msg, 'danger')
            if request.accept_mimetypes.accept_json:
                return jsonify({'success': False, 'errors': form.errors}), 400

    else: # Invalid action
        current_app.logger.warning(f"Invalid action '{action}' received in tag_request.")
        flash('Invalid tag action specified.', 'danger')
        if request.accept_mimetypes.accept_json:
             return jsonify({'success': False, 'message': 'Invalid action specified.'}), 400

    # Default redirect after action (mainly for non-AJAX or errors)
    return redirect(url_for('main.view_request', request_id=request_id))


# --- CANCEL REQUEST ---
@main.route('/cancel_request/<int:request_id>', methods=['POST'])
@login_required
def cancel_request(request_id):
    # Use filter_by for potentially deleted items viewable by Super User
    work_order = WorkOrder.query.filter_by(id=request_id).first_or_404()

    # Permission checks
    is_author = work_order.author == current_user
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']

    if not (is_author or is_property_manager or is_admin_staff):
        abort(403) # Forbidden

    # Use a simple form for CSRF protection
    form = DeleteRestoreRequestForm()
    if form.validate_on_submit():
        if work_order.status in ['Closed', 'Cancelled']:
            flash(f'This request cannot be cancelled as it is already {work_order.status}.', 'warning')
        elif work_order.is_deleted:
             flash('Cannot cancel a deleted request. Restore it first.', 'warning')
        else:
            old_status = work_order.status
            work_order.status = 'Cancelled'
            # Clear related fields on cancellation
            # Keep tags unless specifically required to clear?
            # work_order.tag = None
            work_order.scheduled_date = None
            work_order.follow_up_date = None
            work_order.approved_quote_id = None # Clear approved quote

            log_text = f'Request cancelled by {current_user.name} ({current_user.role}). Status changed from {old_status}.'
            db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash('The request has been successfully cancelled.', 'success')
    else:
         flash('Invalid request to cancel (CSRF validation failed).', 'danger')

    return redirect(url_for('main.view_request', request_id=request_id))


# --- ASSIGN VENDOR ---
@main.route('/assign_vendor/<int:request_id>', methods=['POST'])
@login_required
@admin_required # Only Admin/Scheduler/SuperUser can assign
def assign_vendor(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    # Use AssignVendorForm just for CSRF validation here, get vendor_id from hidden input
    form = AssignVendorForm()
    if form.validate_on_submit():
        vendor_id = request.form.get('vendor_id')
        if not vendor_id:
            flash('No vendor selected.', 'danger')
            return redirect(url_for('main.view_request', request_id=request_id))

        vendor = Vendor.query.get(vendor_id)
        if not vendor:
            flash('Invalid vendor selected.', 'danger')
            return redirect(url_for('main.view_request', request_id=request_id))

        if work_order.vendor_id == vendor.id:
             flash(f"Vendor '{vendor.company_name}' is already assigned.", 'info')
        else:
            work_order.vendor_id = vendor.id
            db.session.add(AuditLog(text=f"Vendor '{vendor.company_name}' assigned.", user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash(f"Vendor '{vendor.company_name}' has been assigned to this request.", 'success')
    else:
         flash('Invalid request to assign vendor (CSRF validation failed).', 'danger')

    return redirect(url_for('main.view_request', request_id=request_id))

# --- UNASSIGN VENDOR ---
@main.route('/unassign_vendor/<int:request_id>', methods=['POST'])
@login_required
@admin_required # Only Admin/Scheduler/SuperUser can unassign
def unassign_vendor(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = DeleteRestoreRequestForm() # Use a simple CSRF form
    if form.validate_on_submit():
        if work_order.vendor:
            vendor_name = work_order.vendor.company_name
            work_order.vendor_id = None
            db.session.add(AuditLog(text=f"Vendor '{vendor_name}' unassigned.", user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash(f"Vendor '{vendor_name}' has been unassigned.", 'success')
        else:
            flash('No vendor was assigned to this request.', 'info')
    else:
        flash('Invalid request to unassign vendor (CSRF validation failed).', 'danger')
    return redirect(url_for('main.view_request', request_id=request_id))


# --- MARK NOTIFICATION AS READ ---
@main.route('/notifications/read/<int:notification_id>')
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.get_or_404(notification_id)
    # Ensure the notification belongs to the current user
    if notification.user_id != current_user.id:
        abort(403) # Forbidden
    # Mark as read only if it's not already read
    if not notification.is_read:
        notification.is_read = True
        db.session.commit()
    # Redirect to the link associated with the notification
    return redirect(notification.link)


# --- CREATE NEW REQUEST ---
@main.route('/new-request', methods=['GET', 'POST'])
@login_required
def new_request():
    properties = Property.query.order_by(Property.name).all()
    # Create dictionary for JS to auto-populate address/manager
    properties_dict = {p.name: {"address": p.address, "manager": p.property_manager} for p in properties}
    form = NewRequestForm()
    # Populate request type choices dynamically
    form.request_type.choices = [(rt.id, rt.name) for rt in RequestType.query.order_by(RequestType.name).all()]

    if form.validate_on_submit():
        try:
            # Date fields are Date type, no timezone conversion needed from form
            date1 = datetime.strptime(form.date_1.data, '%m/%d/%Y').date() if form.date_1.data else None
            date2 = datetime.strptime(form.date_2.data, '%m/%d/%Y').date() if form.date_2.data else None
            date3 = datetime.strptime(form.date_3.data, '%m/%d/%Y').date() if form.date_3.data else None

            selected_property = Property.query.filter_by(name=form.property.data).first()

            # WorkOrder creation, date_created defaults to Denver time via model
            new_order = WorkOrder(
                wo_number=form.wo_number.data,
                requester_name=current_user.name, # Store name for potential user deletion later
                request_type_id=form.request_type.data,
                description=form.description.data,
                property=form.property.data, # Store selected property name
                unit=form.unit.data,
                tenant_name=form.tenant_name.data,
                tenant_phone=form.tenant_phone.data,
                contact_person=form.contact_person.data,
                contact_person_phone=form.contact_person_phone.data,
                preferred_date_1=date1,
                preferred_date_2=date2,
                preferred_date_3=date3,
                user_id=current_user.id, # Link to the current user
                preferred_vendor=form.vendor_assigned.data, # Store preferred vendor name
                status='New' # Initial status
            )

            # Assign property details based on selection or lookup
            if selected_property:
                new_order.property_id = selected_property.id # Link to property record
                new_order.address = selected_property.address
                new_order.property_manager = selected_property.property_manager
            else:
                # If property name doesn't match DB, use auto-populated or manually entered details (less likely now)
                new_order.address = request.form.get('address', '') # Get address from hidden input if needed
                new_order.property_manager = request.form.get('property_manager', '') # Get manager from hidden input

            # Attempt to link vendor based on preferred name
            if form.vendor_assigned.data:
                vendor = Vendor.query.filter(Vendor.company_name.ilike(form.vendor_assigned.data)).first()
                if vendor:
                    new_order.vendor_id = vendor.id # Link if found

            db.session.add(new_order)
            db.session.commit() # Commit to get new_order.id

            # Add creation audit log
            db.session.add(AuditLog(text='Request created.', user_id=current_user.id, work_order_id=new_order.id))
            db.session.commit() # Commit log

            # Save any uploaded attachments
            files_saved = 0
            for file in form.attachments.data:
                 if save_attachment(file, new_order.id):
                      files_saved += 1
            if files_saved > 0:
                 current_app.logger.info(f"Saved {files_saved} attachments for new request {new_order.id}")


            # Send notifications to Admin/Scheduler/Super Users
            admins_and_schedulers = User.query.filter(User.role.in_(['Admin', 'Scheduler', 'Super User'])).all()
            notification_text = f'New request #{new_order.id} submitted by {current_user.name}.'
            notification_link_internal = url_for('main.view_request', request_id=new_order.id)
            notification_link_external = url_for('main.view_request', request_id=new_order.id, _external=True)

            for user in admins_and_schedulers:
                if user != current_user: # Don't notify self
                    notification = Notification(text=notification_text, link=notification_link_internal, user_id=user.id)
                    db.session.add(notification)
                    send_push_notification(user.id, 'New Work Request', notification_text, notification_link_external)
            db.session.commit() # Commit notifications

            flash('Your request has been created successfully!', 'success')
            return redirect(url_for('main.my_requests')) # Redirect user to their list

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating new request: {e}", exc_info=True)
            flash('An error occurred while creating the request. Please try again.', 'danger')

    elif request.method == 'POST': # Handle validation errors on POST
        current_app.logger.warning(f"--- NEW REQUEST FORM VALIDATION FAILED --- Errors: {form.errors}")
        for field, errors in form.errors.items():
             for error in errors:
                  # Use field label if available, otherwise field name
                  label = getattr(getattr(form, field, None), 'label', None)
                  field_name = label.text if label else field.replace('_', ' ').title()
                  flash(f"Error in {field_name}: {error}", 'danger')

    # Render form on GET or after validation error on POST
    return render_template('request_form.html', title='New Request', form=form,
        properties=properties, property_data=json.dumps(properties_dict))


# --- EDIT REQUEST ---
@main.route('/edit-request/<int:request_id>', methods=['GET', 'POST'])
@login_required
def edit_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    # Permissions
    is_author = work_order.author == current_user
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']
    if not (is_author or is_admin_staff):
        flash('You do not have permission to edit this request.', 'danger')
        return redirect(url_for('main.view_request', request_id=request_id))

    # Prevent editing closed/cancelled unless Admin/Super User
    if work_order.status in ['Closed', 'Cancelled'] and not current_user.role in ['Admin', 'Super User']:
        flash(f'This request cannot be edited because it is {work_order.status}.', 'warning')
        return redirect(url_for('main.view_request', request_id=work_order.id))

    properties = Property.query.order_by(Property.name).all()
    properties_dict = {p.name: {"address": p.address, "manager": p.property_manager} for p in properties}
    # Populate form with existing data using obj=work_order
    form = NewRequestForm(obj=work_order)
    form.request_type.choices = [(rt.id, rt.name) for rt in RequestType.query.order_by(RequestType.name).all()]
    reassign_form = ReassignRequestForm() # Form for reassignment section

    del form.attachments # Remove attachments field; handled separately

    if form.validate_on_submit():
        try:
            # --- Store original values for logging changes ---
            original_values = {f.name: getattr(work_order, f.name) for f in form if hasattr(work_order, f.name)}
            original_property_details = {
                 'property': work_order.property,
                 'address': work_order.address,
                 'property_manager': work_order.property_manager
            }

            # --- Update fields from form data ---
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

            # Update dates (convert string from form back to date object)
            work_order.preferred_date_1 = datetime.strptime(form.date_1.data, '%m/%d/%Y').date() if form.date_1.data else None
            work_order.preferred_date_2 = datetime.strptime(form.date_2.data, '%m/%d/%Y').date() if form.date_2.data else None
            work_order.preferred_date_3 = datetime.strptime(form.date_3.data, '%m/%d/%Y').date() if form.date_3.data else None

            # --- Update property relation and details ---
            selected_property = Property.query.filter_by(name=form.property.data).first()
            if selected_property:
                work_order.property_id = selected_property.id
                work_order.address = selected_property.address
                work_order.property_manager = selected_property.property_manager
            else:
                # If property name doesn't match DB, clear relation and use auto-populated details
                work_order.property_id = None
                work_order.address = request.form.get('address', '') # Get from hidden field
                work_order.property_manager = request.form.get('property_manager', '') # Get from hidden field

            # --- Update assigned vendor based on preferred name if changed ---
            # Compare preferred vendor name; if it changed, update vendor_id link
            if original_values.get('preferred_vendor') != form.vendor_assigned.data:
                 if form.vendor_assigned.data:
                     vendor = Vendor.query.filter(Vendor.company_name.ilike(form.vendor_assigned.data)).first()
                     work_order.vendor_id = vendor.id if vendor else None
                 else:
                     work_order.vendor_id = None # Clear if preferred is cleared


            # --- Log specific changes ---
            changes = []
            for field_name, old_value in original_values.items():
                 # Handle request_type_id change logging
                 if field_name == 'request_type_id':
                      old_rt = RequestType.query.get(old_value)
                      new_rt = RequestType.query.get(work_order.request_type_id)
                      old_name = old_rt.name if old_rt else 'None'
                      new_name = new_rt.name if new_rt else 'None'
                      if old_name != new_name:
                           changes.append(f"Request Type: '{old_name}' -> '{new_name}'")
                      continue # Skip default comparison for ID

                 new_value = getattr(work_order, field_name)
                 # Handle date comparison correctly
                 if isinstance(old_value, datetime.date):
                      old_value_str = old_value.strftime('%m/%d/%Y') if old_value else 'None'
                      new_value_str = new_value.strftime('%m/%d/%Y') if new_value else 'None'
                      if old_value_str != new_value_str:
                           changes.append(f"{field_name.replace('_', ' ').title()}: '{old_value_str}' -> '{new_value_str}'")
                 elif old_value != new_value:
                      # Limit length of logged values for description etc.
                      old_val_disp = (str(old_value)[:50] + '...') if isinstance(old_value, str) and len(str(old_value)) > 53 else old_value
                      new_val_disp = (str(new_value)[:50] + '...') if isinstance(new_value, str) and len(str(new_value)) > 53 else new_value
                      changes.append(f"{field_name.replace('_', ' ').title()}: '{old_val_disp}' -> '{new_val_disp}'")

            # Log property detail changes
            if original_property_details['property'] != work_order.property: changes.append(f"Property Name: '{original_property_details['property']}' -> '{work_order.property}'")
            if original_property_details['address'] != work_order.address: changes.append(f"Address: '{original_property_details['address']}' -> '{work_order.address}'")
            if original_property_details['property_manager'] != work_order.property_manager: changes.append(f"Property Manager: '{original_property_details['property_manager']}' -> '{work_order.property_manager}'")

            if changes:
                 log_text = f"Edited request details. Changes: {'; '.join(changes)}."
                 db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
            else:
                 # Log even if no changes detected by comparison, as user submitted the form
                 db.session.add(AuditLog(text='Submitted edit form, no changes detected.', user_id=current_user.id, work_order_id=work_order.id))


            db.session.commit()
            flash('Request has been updated successfully.', 'success')
            return redirect(url_for('main.view_request', request_id=work_order.id))

        except Exception as e:
             db.session.rollback()
             current_app.logger.error(f"Error editing request {request_id}: {e}", exc_info=True)
             flash('An error occurred while saving changes. Please try again.', 'danger')


    elif request.method == 'POST': # Handle validation errors on POST
        current_app.logger.warning(f"--- EDIT REQUEST FORM VALIDATION FAILED --- Errors: {form.errors}")
        for field, errors in form.errors.items():
             for error in errors:
                  label = getattr(getattr(form, field, None), 'label', None)
                  field_name = label.text if label else field.replace('_', ' ').title()
                  flash(f"Error in {field_name}: {error}", 'danger')


    # --- Populate form with existing data on GET request ---
    # WTForms(obj=...) handles most fields, but manually set dates from date objects
    if request.method == 'GET':
        form.date_1.data = work_order.preferred_date_1.strftime('%m/%d/%Y') if work_order.preferred_date_1 else ''
        form.date_2.data = work_order.preferred_date_2.strftime('%m/%d/%Y') if work_order.preferred_date_2 else ''
        form.date_3.data = work_order.preferred_date_3.strftime('%m/%d/%Y') if work_order.preferred_date_3 else ''
        # Ensure request_type is correctly selected
        form.request_type.data = work_order.request_type_id
        # Populate preferred vendor field
        form.vendor_assigned.data = work_order.preferred_vendor


    return render_template('edit_request.html', title='Edit Request', form=form, work_order=work_order,
                           properties=properties, property_data=json.dumps(properties_dict), reassign_form=reassign_form)


# --- UPLOAD ATTACHMENT ---
@main.route('/upload_attachment/<int:request_id>', methods=['POST'])
@login_required
def upload_attachment(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    # Basic permission check: can user view this request?
    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']
    if not (is_author or is_viewer or is_property_manager or is_admin_staff):
         flash('You do not have permission to upload attachments to this request.', 'danger')
         return redirect(url_for('main.view_request', request_id=request_id))


    form = AttachmentForm() # AttachmentForm uses MultipleFileField named 'file'
    if form.validate_on_submit():
        files_uploaded_count = 0
        for file in form.file.data: # Access data attribute of MultipleFileField
            if file and file.filename:
                # Determine file type (default to 'Attachment')
                file_type = request.form.get('file_type', 'Attachment')
                attachment_obj = save_attachment(file, request_id, file_type)

                if attachment_obj:
                    # Log successful upload
                    db.session.add(AuditLog(text=f'Uploaded {file_type}: {secure_filename(file.filename)}', user_id=current_user.id, work_order_id=work_order.id))
                    files_uploaded_count += 1
                else:
                    # Log error if save_attachment failed (it already logs internally)
                    flash(f'Failed to save attachment: {secure_filename(file.filename)}', 'danger')
            # else: file object might be empty if user selected multiple slots but left one blank

        if files_uploaded_count > 0:
             # Commit audit logs for successfully saved files
             try:
                 db.session.commit()
                 flash(f'{files_uploaded_count} attachment(s) uploaded successfully.', 'success')
             except Exception as e:
                  db.session.rollback()
                  current_app.logger.error(f"Error committing attachment audit logs: {e}", exc_info=True)
                  flash('Attachments saved, but failed to log the action.', 'warning')
        else:
             flash('No valid files were selected or uploaded.', 'warning')

    else:
        # Log form validation errors and flash them
        current_app.logger.warning(f"AttachmentForm validation failed: {form.errors}")
        for field, errors in form.errors.items():
            for error_message in errors:
                flash(f"Attachment Error: {error_message}", 'danger')

    return redirect(url_for('main.view_request', request_id=request_id))


# --- DOWNLOAD ATTACHMENT ---
@main.route('/download_attachment/<int:attachment_id>')
@login_required
def download_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    # Check if this attachment is associated with a WorkOrder
    work_order = None
    if attachment.work_order_id:
         work_order = WorkOrder.query.get(attachment.work_order_id)

    if not work_order:
         # Could be attached to something else later, like a message, add checks here
         abort(404) # Not found or not linked correctly


    # Permission check based on WorkOrder association
    can_access = False
    if work_order:
        is_author = work_order.author == current_user
        is_viewer = current_user in work_order.viewers
        is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
        is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']
        can_access = is_author or is_viewer or is_property_manager or is_admin_staff
        # Add check for deleted work orders if needed
        if work_order.is_deleted and current_user.role != 'Super User':
             can_access = False


    if not can_access:
        abort(403) # Forbidden

    # Use the unique filename for serving, but might need original name for download_name
    # Assuming unique filename is stored in attachment.filename
    # If original name was stored separately, use that for download_name=...
    safe_unique_filename = secure_filename(attachment.filename)

    # Try to determine a sensible download name (e.g., prefix with WO ID if original name isn't stored)
    download_name_prefix = f"WO{work_order.id}_" if work_order else ""
    # A simple approach if original name isn't stored: use file type and truncated unique name
    sensible_download_name = f"{download_name_prefix}{attachment.file_type}_{safe_unique_filename[:15]}{os.path.splitext(safe_unique_filename)[1]}"


    return send_from_directory(
         current_app.config['UPLOAD_FOLDER'],
         safe_unique_filename, # Serve the unique filename from storage
         as_attachment=True,
         download_name=sensible_download_name # Suggest a user-friendly name
     )


# --- VIEW ATTACHMENT (IN BROWSER) ---
@main.route('/view_attachment/<int:attachment_id>')
@login_required
def view_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    # Check association (similar to download)
    work_order = None
    if attachment.work_order_id:
         work_order = WorkOrder.query.get(attachment.work_order_id)

    if not work_order:
        abort(404)

    # Permission check (similar to download)
    can_access = False
    if work_order:
        is_author = work_order.author == current_user
        is_viewer = current_user in work_order.viewers
        is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
        is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']
        can_access = is_author or is_viewer or is_property_manager or is_admin_staff
        if work_order.is_deleted and current_user.role != 'Super User':
             can_access = False

    if not can_access:
        abort(403)

    # Determine mimetype to tell the browser how to display it
    safe_unique_filename = secure_filename(attachment.filename)
    mimetype, _ = mimetypes.guess_type(safe_unique_filename)
    if not mimetype: # Provide a default if guess fails
        mimetype = 'application/octet-stream' # Browser will likely download if unknown

    # Serve the file inline
    return send_from_directory(
        current_app.config['UPLOAD_FOLDER'],
        safe_unique_filename,
        as_attachment=False, # Important: Serve inline
        mimetype=mimetype
    )


# --- DELETE ATTACHMENT ---
@main.route('/delete_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    # Permission: Uploader or Admin/Scheduler/Super User
    # Note: 'Admin' was in original check, 'Scheduler' added for consistency
    if attachment.user_id != current_user.id and current_user.role not in ['Admin', 'Scheduler', 'Super User']:
        abort(403) # Forbidden

    # Determine redirect target (Work Order view or fallback)
    redirect_url = url_for('main.index') # Default fallback
    work_order_id = attachment.work_order_id
    if work_order_id:
        redirect_url = url_for('main.view_request', request_id=work_order_id)

    form = DeleteRestoreRequestForm() # Use for CSRF validation
    if form.validate_on_submit():
        original_filename_stored = attachment.filename # Keep unique name for file path
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], original_filename_stored)

        try:
            # Delete physical file first
            if os.path.exists(file_path):
                os.remove(file_path)
                current_app.logger.info(f"Deleted attachment file: {file_path}")
            else:
                 current_app.logger.warning(f"Attachment file not found for deletion: {file_path}")

            # Log deletion (only if associated with a work order)
            if work_order_id:
                # Use a generic name or reconstruct original if stored elsewhere
                display_name = original_filename_stored # Or original name if available
                db.session.add(AuditLog(text=f'Deleted attachment: {display_name}', user_id=current_user.id, work_order_id=work_order_id))

            # Delete DB record
            db.session.delete(attachment)
            db.session.commit()
            flash('Attachment deleted successfully.', 'success')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error deleting attachment {attachment_id}: {e}", exc_info=True)
            flash('Error deleting attachment.', 'danger')
    else:
        flash('Invalid request to delete attachment (CSRF validation failed).', 'danger')
        # Try redirecting back to referrer if CSRF fails
        redirect_url = request.referrer or redirect_url

    return redirect(redirect_url)


# --- ACCOUNT MANAGEMENT ---
@main.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    update_form = UpdateAccountForm(obj=current_user)
    password_form = ChangePasswordForm()

    # --- Handle Account Update ---
    if 'update_account' in request.form:
        if update_form.validate_on_submit():
            try:
                current_user.name = update_form.name.data
                current_user.email = update_form.email.data

                # --- Handle Signature Update (for allowed roles) ---
                if current_user.role in ['Admin', 'Scheduler', 'Super User', 'Property Manager']:
                    # Define allowed HTML tags and attributes for sanitization
                    allowed_tags = [
                        'a', 'abbr', 'acronym', 'b', 'blockquote', 'code', 'em', 'i', 'strong',
                        'li', 'ol', 'ul', 'br', 'p', 'img', 'span', 'div', 'font',
                        'table', 'tbody', 'thead', 'tr', 'td', 'th', 'figure', 'figcaption',
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'pre', 'sub', 'sup', 'u' # Added more formatting
                    ]
                    allowed_attrs = {
                        '*': ['style', 'class', 'align', 'valign', 'width', 'height', 'cellpadding', 'cellspacing', 'border'],
                        'a': ['href', 'title', 'target'],
                        'img': ['src', 'alt', 'width', 'height', 'style'], # Ensure 'style' is allowed for images
                        'font': ['color', 'face', 'size']
                        # Add specific style properties if needed, e.g., 'p': ['style'] requires careful validation
                    }
                    allowed_protocols = ['http', 'https', 'mailto', 'data'] # Allow data URI for embedded images

                    signature_html = request.form.get('signature', '') # Get raw HTML from textarea/editor

                    # Function to find local images and embed them as base64 data URIs
                    def embed_local_images(html_content):
                        upload_folder = current_app.config['UPLOAD_FOLDER']
                        # Regex to find image URLs pointing to our /uploads/ endpoint
                        img_tags = re.findall(r'<img[^>]+src=[\'"](https?://[^/]+/uploads/([^\'"]+))[\'"]', html_content)

                        for full_url, filename_part in img_tags:
                            filename = filename_part.split('?')[0] # Remove potential query params
                            filepath = os.path.join(upload_folder, filename)

                            if os.path.exists(filepath):
                                try:
                                    with open(filepath, "rb") as image_file:
                                         encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

                                    mime_type, _ = mimetypes.guess_type(filepath)
                                    if not mime_type: # Guess common types if mimetypes fails
                                         ext = os.path.splitext(filename)[1].lower()
                                         mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
                                         mime_type = mime_map.get(ext, 'application/octet-stream')

                                    data_uri = f"data:{mime_type};base64,{encoded_string}"
                                    # Replace the exact URL found with the data URI
                                    html_content = html_content.replace(full_url, data_uri, 1)
                                except Exception as e:
                                    current_app.logger.error(f"Error embedding signature image {filename}: {e}")
                        return html_content

                    # Embed local images before cleaning
                    embedded_html = embed_local_images(signature_html)

                    # Clean the HTML (including data URIs)
                    clean_html = bleach.clean(
                        embedded_html,
                        tags=allowed_tags,
                        attributes=allowed_attrs,
                        protocols=allowed_protocols,
                        strip=False # Keep legitimate tags, just clean attributes/protocols
                    )
                    current_user.signature = clean_html

                db.session.commit()
                flash('Your account has been updated!', 'success')
                return redirect(url_for('main.account')) # Redirect after successful update
            except Exception as e:
                 db.session.rollback()
                 current_app.logger.error(f"Error updating account for user {current_user.id}: {e}", exc_info=True)
                 flash('An error occurred while updating your account.', 'danger')
        else:
             # Log validation errors if update fails
             current_app.logger.warning(f"UpdateAccountForm validation failed: {update_form.errors}")
             for field, errors in update_form.errors.items():
                for error in errors:
                    flash(f"Update Error: {error}", 'danger')

    # --- Handle Password Change ---
    elif 'change_password' in request.form:
        if password_form.validate_on_submit():
            if current_user.check_password(password_form.current_password.data):
                try:
                    current_user.set_password(password_form.new_password.data)
                    db.session.commit()
                    flash('Your password has been changed successfully!', 'success')
                except Exception as e:
                     db.session.rollback()
                     current_app.logger.error(f"Error changing password for user {current_user.id}: {e}", exc_info=True)
                     flash('An error occurred while changing your password.', 'danger')
            else:
                flash('Incorrect current password.', 'danger')
            # Redirect even on failure to clear form fields
            return redirect(url_for('main.account'))
        else:
             # Log validation errors if password change fails
             current_app.logger.warning(f"ChangePasswordForm validation failed: {password_form.errors}")
             # Flash only generic error, not specific password rules
             if 'current_password' not in password_form.errors: # Only flash if not the 'incorrect password' case
                  flash('Password change failed. Please check the requirements.', 'danger')


    # Render template on GET or after failed POST validation
    return render_template('account.html', title='Account', update_form=update_form, password_form=password_form)


# --- CKEDITOR IMAGE UPLOAD ENDPOINT ---
@main.route('/upload_image', methods=['POST'])
@csrf.exempt # Exempt CSRF for CKEditor SimpleUpload adapter compatibility
@login_required
def upload_image():
    if 'upload' not in request.files:
        return jsonify({'uploaded': 0, 'error': {'message': 'No upload file found in request.'}}), 400

    file = request.files['upload']
    # Basic validation
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    if not file or not file.filename or '.' not in file.filename or \
       file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({'uploaded': 0, 'error': {'message': 'Invalid file type. Allowed: png, jpg, jpeg, gif, webp'}}), 400

    try:
        filename = secure_filename(file.filename)
        ext = filename.rsplit('.', 1)[1].lower()
        # Create a unique filename using UUID
        unique_filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(filepath)
        current_app.logger.info(f"Image uploaded for CKEditor: {filepath}")

        # Generate URL for the uploaded file (use external=True for absolute URL)
        # Add a timestamp query param to potentially bypass browser cache issues
        cache_buster = int(get_denver_now().timestamp())
        file_url = url_for('main.uploaded_file', filename=unique_filename, v=cache_buster, _external=True)

        # CKEditor SimpleUpload adapter expects this JSON response format
        return jsonify({'uploaded': 1, 'fileName': unique_filename, 'url': file_url})

    except Exception as e:
        current_app.logger.error(f"Error saving CKEditor uploaded image: {e}", exc_info=True)
        return jsonify({'uploaded': 0, 'error': {'message': 'Server error during image upload.'}}), 500

# --- SERVE UPLOADED FILES (Including CKEditor Images) ---
@main.route('/uploads/<filename>')
# @login_required # Consider if images should be public or require login
def uploaded_file(filename):
    # Basic security check: prevent directory traversal
    if '..' in filename or filename.startswith('/'):
        abort(404)
    # Serve file from the configured UPLOAD_FOLDER
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


# --- REPORTS PAGE ---
@main.route('/reports')
@login_required
@admin_required # Ensure only authorized users access reports
def reports_page():
    form = ReportForm()
    return render_template('reports.html', title='Reports', form=form)

# --- DOWNLOAD ALL WORK ORDERS REPORT ---
@main.route('/reports/download/all_work_orders')
@login_required
@admin_required
def download_all_work_orders():
    # Get filter parameters from query string
    date_type = request.args.get('date_type', 'date_created')
    date_range_key = request.args.get('date_range', 'all')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    # Base query
    query = WorkOrder.query

    # Get date range using helper function
    start_dt, end_dt = get_date_range(date_range_key, start_date_str, end_date_str)

    # Apply date filter based on selected date type (created or completed)
    date_column = getattr(WorkOrder, date_type, WorkOrder.date_created) # Default safely
    if start_dt:
        query = query.filter(date_column >= start_dt)
    if end_dt:
        query = query.filter(date_column <= end_dt)

    # Fetch and order results
    work_orders = query.order_by(WorkOrder.date_created.asc()).all()

    # --- Generate CSV ---
    string_io = io.StringIO()
    csv_writer = csv.writer(string_io)
    headers = [
        'ID', 'WO Number', 'Status', 'Tags', 'Vendor Assigned', 'Preferred Vendor',
        'Date Created (App Time)', 'Date Completed (App Time)', 'Scheduled Date',
        'Follow-up Date', 'Requester', 'Request Type', 'Property', 'Unit',
        'Address', 'Property Manager', 'Tenant Name', 'Tenant Phone',
        'Contact Person', 'Contact Person Phone', 'Description'
    ]
    csv_writer.writerow(headers)

    for wo in work_orders:
        # Convert DB times to application timezone (Denver) before formatting
        created_dt_app = convert_to_denver(wo.date_created)
        completed_dt_app = convert_to_denver(wo.date_completed)

        # Format datetimes (e.g., MM/DD/YYYY HH:MM)
        created_str = created_dt_app.strftime('%m/%d/%Y %H:%M') if created_dt_app else ''
        completed_str = completed_dt_app.strftime('%m/%d/%Y %H:%M') if completed_dt_app else ''
        # Format dates (MM/DD/YYYY)
        scheduled_str = wo.scheduled_date.strftime('%m/%d/%Y') if wo.scheduled_date else ''
        follow_up_str = wo.follow_up_date.strftime('%m/%d/%Y') if wo.follow_up_date else ''


        csv_writer.writerow([
            wo.id, wo.wo_number, wo.status, wo.tag,
            wo.vendor.company_name if wo.vendor else '', wo.preferred_vendor,
            created_str, completed_str, scheduled_str, follow_up_str,
            wo.requester_name, wo.request_type_relation.name if wo.request_type_relation else '',
            wo.property, wo.unit, wo.address, wo.property_manager,
            wo.tenant_name, wo.tenant_phone, wo.contact_person, wo.contact_person_phone,
            wo.description
        ])

    output = string_io.getvalue()
    string_io.close()

    # --- Generate filename with filter info ---
    filename_parts = ["work_orders"]
    if date_range_key != 'all':
         filename_parts.append(date_range_key)
    if start_date_str: filename_parts.append(f"from_{start_date_str.replace('/', '-')}")
    if end_date_str: filename_parts.append(f"to_{end_date_str.replace('/', '-')}")
    filename_parts.append(f"by_{date_type}")
    filename = "_".join(filename_parts) + ".csv"

    # Return CSV file as response
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

# --- DOWNLOAD SUMMARY REPORT ---
@main.route('/reports/download/summary')
@login_required
@admin_required
def download_summary_report():
    # Get filter parameters
    date_type = request.args.get('date_type', 'date_created')
    date_range_key = request.args.get('date_range', 'all')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    start_dt, end_dt = get_date_range(date_range_key, start_date_str, end_date_str)

    # Base query and apply date filter
    base_query = WorkOrder.query.filter_by(is_deleted=False) # Exclude deleted
    date_column = getattr(WorkOrder, date_type, WorkOrder.date_created) # Default safely
    if start_dt:
        base_query = base_query.filter(date_column >= start_dt)
    if end_dt:
        base_query = base_query.filter(date_column <= end_dt)

    filtered_orders = base_query.all() # Fetch filtered data

    # Calculate summaries using Counters
    status_counts = Counter(req.status for req in filtered_orders)
    type_counts = Counter(req.request_type_relation.name for req in filtered_orders if req.request_type_relation)
    property_counts = Counter(req.property for req in filtered_orders)

    # --- Generate CSV ---
    string_io = io.StringIO()
    csv_writer = csv.writer(string_io)

    csv_writer.writerow(['Summary by Status'])
    csv_writer.writerow(['Status', 'Count'])
    for status, count in sorted(status_counts.items()): # Sort for consistency
        csv_writer.writerow([status, count])
    csv_writer.writerow([]) # Blank row separator

    csv_writer.writerow(['Summary by Request Type'])
    csv_writer.writerow(['Type', 'Count'])
    for req_type, count in sorted(type_counts.items()):
        csv_writer.writerow([req_type, count])
    csv_writer.writerow([])

    csv_writer.writerow(['Summary by Property'])
    csv_writer.writerow(['Property', 'Count'])
    for prop, count in sorted(property_counts.items()):
        csv_writer.writerow([prop, count])

    output = string_io.getvalue()
    string_io.close()

    # --- Generate filename ---
    filename_parts = ["summary_report"]
    if date_range_key != 'all': filename_parts.append(date_range_key)
    if start_date_str: filename_parts.append(f"from_{start_date_str.replace('/', '-')}")
    if end_date_str: filename_parts.append(f"to_{end_date_str.replace('/', '-')}")
    filename_parts.append(f"by_{date_type}")
    filename = "_".join(filename_parts) + ".csv"

    # Return response
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


# --- CALENDAR VIEW ---
@main.route('/calendar')
@login_required
def calendar():
    # Just render the template; data is fetched via API
    return render_template('calendar.html', title='Calendar')


# --- CALENDAR EVENTS API ---
@main.route('/api/events')
@login_required
def api_events():
    # Colors for different event types/statuses
    status_colors = {
        'Scheduled': '#8B5CF6',  # purple
        'New': '#3B82F6',        # blue
        'Open': '#10B981',       # green
        'Pending': '#F59E0B',    # yellow
        'Follow-up': '#EF4444'   # red (Used for Follow-up date events)
        # Add other statuses if they should appear differently on calendar
    }

    event_list = []

    # --- Fetch Scheduled Events ---
    # Base query for scheduled, non-deleted orders
    query_scheduled = WorkOrder.query.filter(WorkOrder.scheduled_date.isnot(None), WorkOrder.is_deleted==False)
    # Apply user-specific filters if needed (e.g., only show user's requests)
    if current_user.role == 'Requester':
        query_scheduled = query_scheduled.filter(WorkOrder.user_id == current_user.id)
    elif current_user.role == 'Property Manager':
         # Show requests where PM is assigned OR requests they authored? Decide based on requirements.
         # Example: Show requests where PM is assigned:
         query_scheduled = query_scheduled.filter(WorkOrder.property_manager == current_user.name)

    events_scheduled = query_scheduled.all()

    for event in events_scheduled:
        # Scheduled date is stored as Date object (naive)
        # Convert to app-local datetime at start of day for FullCalendar
        start_local_iso = format_app_dt(make_denver_aware_start_of_day(event.scheduled_date)) if event.scheduled_date else None
        if start_local_iso: # Only add if date is valid
            event_list.append({
                'title': f"#{event.id} - {event.property}", # Event title
                'start': start_local_iso,  # ISO 8601 string in app timezone
                'allDay': True, # Mark as all-day event
                'url': url_for('main.view_request', request_id=event.id), # Link to request view
                'color': status_colors.get(event.status, '#6B7280'), # Color based on status, default gray
                'extendedProps': { # Additional data for tooltips/popups
                    'requester': event.requester_name,
                    'status': event.status,
                    'type': 'Scheduled' # Distinguish type
                }
            })

    # --- Fetch Follow-up Events ---
    # Base query for follow-up dates, non-deleted
    query_follow_up = WorkOrder.query.filter(WorkOrder.follow_up_date.isnot(None), WorkOrder.is_deleted==False)
    # Apply user filters consistent with scheduled events
    if current_user.role == 'Requester':
         query_follow_up = query_follow_up.filter(WorkOrder.user_id == current_user.id)
    elif current_user.role == 'Property Manager':
         query_follow_up = query_follow_up.filter(WorkOrder.property_manager == current_user.name)

    follow_up_events = query_follow_up.all()

    for event in follow_up_events:
        # Follow-up date is stored as Date object (naive)
        start_local_iso = format_app_dt(make_denver_aware_start_of_day(event.follow_up_date)) if event.follow_up_date else None
        if start_local_iso:
            event_list.append({
                'title': f"Follow-up for #{event.id}",
                'start': start_local_iso,
                'allDay': True,
                'url': url_for('main.view_request', request_id=event.id),
                'color': status_colors.get('Follow-up'), # Specific color for follow-ups
                'extendedProps': {
                    'requester': event.requester_name,
                    'status': event.status, # Show current status
                    'type': 'Follow-up'
                }
            })

    return jsonify(event_list) # Return the list of events as JSON


# --- VENDOR SEARCH API ---
@main.route('/api/vendors/search')
@login_required
def search_vendors():
    q = request.args.get('q', '').strip() # Get search query, default to empty string
    if q and len(q) >= 2: # Only search if query is at least 2 characters
        # Case-insensitive search on company name
        vendors = Vendor.query.filter(Vendor.company_name.ilike(f'%{q}%')).order_by(Vendor.company_name).limit(20).all()
        # Return list of vendor objects with relevant details
        return jsonify([{
            'id': v.id,
            'company_name': v.company_name,
            'contact_name': v.contact_name,
            'email': v.email,
            'phone': v.phone,
            'specialty': v.specialty,
            'website': v.website
            } for v in vendors])
    return jsonify([]) # Return empty list if no query or query too short


# --- USER SEARCH API (for @mentions) ---
@main.route('/api/users/search')
@login_required
def api_user_search():
    # Fetch all active users, ordered by name
    users = User.query.filter_by(is_active=True).order_by(User.name).all()
    # Format for Tribute.js:
    # 'key' is the value displayed and inserted (full name with space)
    # 'value' is used for internal matching/lookup (name without space)
    user_list = [{'key': user.name, 'value': user.name.replace(' ', '')} for user in users]
    return jsonify(user_list)


# --- SEND WORK ORDER AS EMAIL ---
@main.route('/request/<int:request_id>/send_email', methods=['POST'])
@login_required
@admin_required # Ensure only authorized users can send emails this way
def send_work_order_email(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)

    # --- Extract data from form ---
    recipient = request.form.get('recipient')
    cc = request.form.get('cc')
    subject = request.form.get('subject')
    body_html = request.form.get('body') # HTML content from CKEditor
    files = request.files.getlist('attachments') # Get list of uploaded files

    if not recipient:
        return jsonify({'success': False, 'message': 'Recipient email is required.'}), 400

    recipients = [r.strip() for r in recipient.split(',') if r.strip()] # Allow multiple recipients
    cc_list = [email.strip() for email in cc.split(',') if email.strip()] if cc else []

    # --- Process Attachments ---
    attachments_for_email = []
    temp_upload_path = None # Path to temporary directory for this email's attachments
    if files:
        # Create a unique temporary directory
        temp_dir_name = f"temp_email_{uuid.uuid4().hex}"
        temp_upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], temp_dir_name)
        try:
            os.makedirs(temp_upload_path, exist_ok=True)
            for file in files:
                if file and file.filename:
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(temp_upload_path, filename)
                    file.save(filepath)
                    attachments_for_email.append({
                        'path': filepath, # Full path to temp file
                        'filename': filename, # Original (secured) filename for display
                        'mimetype': file.mimetype or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                    })
        except Exception as e:
             current_app.logger.error(f"Error saving temporary email attachments: {e}", exc_info=True)
             # Clean up if error occurs during saving
             if temp_upload_path and os.path.exists(temp_upload_path):
                 import shutil
                 shutil.rmtree(temp_upload_path, ignore_errors=True)
             return jsonify({'success': False, 'message': 'Error preparing attachments.'}), 500

    # --- Prepare Email Body ---
    # Generate plain text version from HTML for email clients that don't support HTML
    text_version_of_body = bleach.clean(body_html, tags=[], strip=True).strip()
    # You might want a dedicated plain text template if formatting is complex
    text_body = text_version_of_body # Simple conversion for now

    # Use HTML template for the main body
    html_body_rendered = render_template('email/work_order_email.html', body=body_html) # Pass HTML directly


    # --- Send Email ---
    try:
        send_notification_email(
            subject=subject,
            recipients=recipients,
            cc=cc_list,
            text_body=text_body,
            html_body=html_body_rendered, # Use rendered HTML template
            attachments=attachments_for_email
        )

        # Log the action
        recipients_str = ", ".join(recipients)
        cc_str = f" (CC: {', '.join(cc_list)})" if cc_list else ""
        db.session.add(AuditLog(text=f"Work order emailed to {recipients_str}{cc_str}", user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()

        return jsonify({'success': True, 'message': 'Email sent successfully!'})

    except Exception as e:
        current_app.logger.error(f"Error sending work order email for request {request_id}: {e}", exc_info=True)
        db.session.rollback() # Rollback audit log if email fails
        return jsonify({'success': False, 'message': 'Failed to send email due to a server error.'}), 500

    finally:
        # --- Clean up temporary attachments ---
        if temp_upload_path and os.path.exists(temp_upload_path):
             import shutil
             try:
                 shutil.rmtree(temp_upload_path)
                 current_app.logger.debug(f"Cleaned up temporary directory: {temp_upload_path}")
             except Exception as e:
                  current_app.logger.error(f"Error cleaning up temp directory {temp_upload_path}: {e}")


# --- ADD QUOTE ---
@main.route('/request/<int:request_id>/add_quote', methods=['POST'])
@login_required
@admin_required # Permissions for adding quotes
def add_quote(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = QuoteForm()
    if form.validate_on_submit():
        vendor = form.vendor.data
        file = form.quote_file.data

        # Save the file first using save_attachment helper
        attachment_obj = save_attachment(file, work_order.id, file_type='Quote')

        if attachment_obj:
            try:
                # Create Quote record, linking the attachment
                # date_sent defaults to Denver time via model
                quote = Quote(
                    work_order_id=work_order.id,
                    vendor_id=vendor.id,
                    attachment_id=attachment_obj.id,
                    status='Pending' # Initial status
                )
                db.session.add(quote)
                # Log the quote upload
                db.session.add(AuditLog(text=f"Quote '{secure_filename(file.filename)}' for vendor '{vendor.company_name}' uploaded.", user_id=current_user.id, work_order_id=work_order.id))
                db.session.commit()
                flash(f'Quote for {vendor.company_name} uploaded successfully.', 'success')
            except Exception as e:
                 db.session.rollback()
                 current_app.logger.error(f"Error saving quote record for request {request_id}: {e}", exc_info=True)
                 flash('Error saving quote record to database.', 'danger')
                 # Attempt to delete the orphaned attachment file if DB save fails
                 if attachment_obj:
                      file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment_obj.filename)
                      if os.path.exists(file_path):
                           try: os.remove(file_path)
                           except Exception: pass # Ignore error during cleanup
        else:
            flash('There was an error saving the quote file.', 'danger')
    else:
        # Flash validation errors
        for field, errors in form.errors.items():
            for error_message in errors:
                label = getattr(getattr(form, field, None), 'label', None)
                field_name = label.text if label else field.replace('_', ' ').title()
                flash(f"Error in {field_name}: {error_message}", 'danger')

    return redirect(url_for('main.view_request', request_id=request_id))


# --- DELETE QUOTE ---
@main.route('/request/delete_quote/<int:quote_id>', methods=['POST'])
@login_required
@admin_required # Permissions for deleting quotes
def delete_quote(quote_id):
    quote = Quote.query.get_or_404(quote_id)
    work_order_id = quote.work_order_id # Get WO ID before deleting quote
    attachment = Attachment.query.get(quote.attachment_id) # Get linked attachment
    form = DeleteRestoreRequestForm() # Use for CSRF protection

    if form.validate_on_submit():
        try:
            vendor_name = quote.vendor.company_name if quote.vendor else 'Unknown Vendor'
            attachment_filename = attachment.filename if attachment else 'Unknown File'

            # --- Delete physical file associated with the attachment ---
            if attachment:
                file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment.filename)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        current_app.logger.info(f"Deleted quote file: {file_path}")
                    except OSError as e:
                        current_app.logger.error(f"Error deleting quote file {attachment.filename}: {e}")
                        # Continue deletion even if file removal fails? Yes.
                else:
                    current_app.logger.warning(f"Quote file not found for deletion: {file_path}")

            # --- Clear approved_quote_id if this was the approved one ---
            work_order = WorkOrder.query.get(work_order_id)
            if work_order and work_order.approved_quote_id == quote_id:
                work_order.approved_quote_id = None
                db.session.add(AuditLog(text="Approved quote reference cleared due to quote deletion.", user_id=current_user.id, work_order_id=work_order_id))
                # Re-evaluate tags after deletion
                other_quotes_approved = Quote.query.filter(Quote.work_order_id == work_order_id, Quote.id != quote_id, Quote.status == 'Approved').count() > 0
                current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])
                if not other_quotes_approved:
                    current_tags.discard('Approved')
                    # Decide if 'Declined' should be added - probably not on deletion
                work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None


            # --- Delete DB records (Attachment first, then Quote) ---
            # Cascade delete should handle quote deletion when attachment is deleted if setup correctly,
            # but explicit deletion is safer. Delete Quote first due to FK.
            db.session.delete(quote)
            if attachment:
                 db.session.delete(attachment)

            # --- Add Audit Log ---
            db.session.add(AuditLog(text=f"Deleted quote '{attachment_filename}' from vendor '{vendor_name}'.", user_id=current_user.id, work_order_id=work_order_id))

            db.session.commit()
            flash('Quote has been deleted successfully.', 'success')

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error deleting quote {quote_id}: {e}", exc_info=True)
            flash('An error occurred while deleting the quote.', 'danger')
    else:
        flash('Invalid request to delete quote (CSRF validation failed).', 'danger')

    return redirect(url_for('main.view_request', request_id=work_order_id))


# --- SEND FOLLOW-UP REMINDERS (Called by scheduler/CLI) ---
def send_reminders():
    """Finds work orders needing follow-up and sends notifications."""
    app = current_app._get_current_object() # Get app instance for context
    with app.app_context():
        # Use Denver's current date for comparison
        today = get_denver_now().date()
        # Find non-deleted work orders tagged for follow-up with date <= today
        work_orders_for_follow_up = WorkOrder.query.filter(
            WorkOrder.follow_up_date <= today,
            WorkOrder.tag.like('%Follow-up needed%'),
            WorkOrder.is_deleted == False
        ).all()

        current_app.logger.info(f"SCHEDULER: Found {len(work_orders_for_follow_up)} work orders for follow-up reminder.")

        if not work_orders_for_follow_up:
            return # Nothing to do

        # Get Admin/Scheduler/Super Users to notify
        admins_and_schedulers = User.query.filter(User.role.in_(['Admin', 'Scheduler', 'Super User'])).all()
        if not admins_and_schedulers:
             current_app.logger.warning("SCHEDULER: No Admin/Scheduler/Super Users found to send reminders to.")
             return


        # Find a system user (e.g., Super User) to attribute the audit log to
        audit_user = User.query.filter_by(role='Super User').first()
        # Fallback to user ID 1 if no Super User exists (assuming ID 1 is an admin)
        audit_user_id = audit_user.id if audit_user else 1

        for wo in work_orders_for_follow_up:
            current_app.logger.info(f"SCHEDULER: Processing reminder for WO #{wo.id}")
            notification_text = f"Follow-up reminder for Request #{wo.id} ({wo.property})"
            notification_link_internal = url_for('main.view_request', request_id=wo.id)
            notification_link_external = url_for('main.view_request', request_id=wo.id, _external=True)

            # Send notifications to each admin/scheduler/super user
            for user in admins_and_schedulers:
                # Create DB Notification
                notification = Notification(text=notification_text, link=notification_link_internal, user_id=user.id)
                db.session.add(notification)
                current_app.logger.info(f"SCHEDULER: Added DB notification for user {user.id} for WO #{wo.id}")

                # Send Push Notification
                send_push_notification(user.id, 'Follow-up Reminder', notification_text, notification_link_external)

                # Send Email Notification
                email_body = f"<p>This is a reminder to follow-up on Request #{wo.id} for property <b>{wo.property}</b>.</p>"
                send_notification_email(
                    subject=f"Follow-up Reminder for Request #{wo.id}", recipients=[user.email],
                    text_body=notification_text,
                    html_body=render_template('email/notification_email.html', title="Follow-up Reminder", user=user, body_content=email_body, link=notification_link_external)
                )

            # --- Update the Work Order: Remove tag and clear date ---
            current_tags = set(wo.tag.split(',') if wo.tag and wo.tag.strip() else [])
            current_tags.discard('Follow-up needed')
            wo.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
            wo.follow_up_date = None
            wo.last_follow_up_sent = get_denver_now() # Record when reminder was sent

            # Add Audit Log attributed to system/admin user
            db.session.add(AuditLog(text="Automated follow-up reminder sent; tag/date cleared.", user_id=audit_user_id, work_order_id=wo.id))
            current_app.logger.info(f"SCHEDULER: Removed follow-up tag and date for WO #{wo.id}")

        # Commit all changes after processing all reminders
        try:
            db.session.commit()
            current_app.logger.info("SCHEDULER: Committed reminder updates.")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"SCHEDULER: Error committing reminder updates: {e}", exc_info=True)


# --- SUBSCRIBE TO PUSH NOTIFICATIONS ---
@main.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    current_app.logger.info(f"DEBUG SUB: Entered /subscribe route for user: {current_user.name}")
    if not request.is_json:
        current_app.logger.warning("DEBUG SUB: /subscribe called without JSON data.")
        return jsonify({'success': False, 'message': 'Request must be JSON.'}), 400

    subscription_data = request.get_json()
    if not subscription_data or 'endpoint' not in subscription_data:
        current_app.logger.warning(f"DEBUG SUB: Invalid or missing subscription data: {subscription_data}")
        return jsonify({'success': False, 'message': 'Invalid subscription data structure.'}), 400

    # Store the subscription data as a JSON string
    subscription_json = json.dumps(subscription_data)

    # Check if this exact subscription already exists for this user
    subscription = PushSubscription.query.filter_by(
        subscription_json=subscription_json,
        user_id=current_user.id
    ).first()

    if not subscription:
        current_app.logger.info(f"DEBUG SUB: New subscription detected for user {current_user.name}. Saving to DB.")
        try:
            new_subscription = PushSubscription(
                subscription_json=subscription_json,
                user_id=current_user.id
            )
            db.session.add(new_subscription)
            db.session.commit()
            current_app.logger.info(f"DEBUG SUB: Successfully saved new subscription id={new_subscription.id} for user {current_user.name}.")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"DEBUG SUB: Error saving subscription to DB for user {current_user.id}: {e}", exc_info=True)
            return jsonify({'success': False, 'message': 'Error saving subscription.'}), 500
    else:
        current_app.logger.info(f"DEBUG SUB: Subscription already exists (id={subscription.id}) for user {current_user.name}.")

    return jsonify({'success': True})


# --- PROVIDE VAPID PUBLIC KEY ---
@main.route('/vapid_public_key', methods=['GET'])
# No login required? Service worker might fetch before user logs in.
# Consider security implications if key should be protected. For now, assume public.
def vapid_public_key():
    """Return the VAPID public key as JSON."""
    key = current_app.config.get('VAPID_PUBLIC_KEY')
    if not key:
        current_app.logger.error('VAPID_PUBLIC_KEY requested but not configured on server.')
        return jsonify({'success': False, 'message': 'VAPID public key not configured on server.'}), 500
    current_app.logger.debug('DEBUG SUB: VAPID_PUBLIC_KEY served to client.')
    return jsonify({'success': True, 'vapidPublicKey': key})


# --- TEST PUSH NOTIFICATION (for debugging) ---
@main.route('/test_push', methods=['GET']) # Changed to GET for simple browser testing
@login_required
def test_push():
    """Triggers a test push notification to the current logged-in user."""
    try:
        current_app.logger.info(f"Attempting test push for user: {current_user.name} (id={current_user.id})")
        title = 'Test Notification'
        body = f'This is a test push sent to {current_user.name} at {get_denver_now().strftime("%I:%M:%S %p")}.'
        link = url_for('main.index', _external=True) # Link back to dashboard/my requests

        # Call the send function in the background (using threading implicitly if called directly)
        send_push_notification(current_user.id, title, body, link)

        flash('Test push notification initiated. Check your device(s). It might take a moment.', 'info')
    except Exception as e:
        current_app.logger.error(f"ERROR in /test_push route for user {current_user.id}: {e}", exc_info=True)
        flash(f'An error occurred while trying to send the test push: {e}', 'danger')

    # Redirect back to the previous page or the index
    return redirect(request.referrer or url_for('main.index'))


# --- VIEW MY SUBSCRIPTIONS (for debugging) ---
@main.route('/my_subscriptions', methods=['GET'])
@login_required
def my_subscriptions():
    """Displays the current user's stored push subscriptions."""
    try:
        subs = PushSubscription.query.filter_by(user_id=current_user.id).order_by(PushSubscription.id).all()
        subscriptions_data = []
        for s in subs:
            try:
                parsed = json.loads(s.subscription_json)
                endpoint_short = parsed.get('endpoint', 'N/A')[:60] + '...' if parsed.get('endpoint') else 'N/A'
                # Extract key parts for display if they exist
                keys = parsed.get('keys', {})
                p256dh = keys.get('p256dh', 'N/A')[:10] + '...' if keys.get('p256dh') else 'N/A'
                auth = keys.get('auth', 'N/A')[:10] + '...' if keys.get('auth') else 'N/A'
            except Exception:
                endpoint_short = 'Error parsing JSON'
                p256dh = 'Error'
                auth = 'Error'

            subscriptions_data.append({
                'id': s.id,
                'endpoint_short': endpoint_short,
                'p256dh_short': p256dh,
                'auth_short': auth,
                })
        # Render a simple debug template
        # Ensure 'debug/subscriptions.html' exists in your templates folder
        return render_template('debug/subscriptions.html', title='My Push Subscriptions', subscriptions=subscriptions_data)
    except Exception as e:
        current_app.logger.error(f"ERROR in my_subscriptions for user {current_user.id}: {e}", exc_info=True)
        flash(f'Error retrieving subscriptions: {e}', 'danger')
        # Redirect to a safe page like account or index on error
        return redirect(url_for('main.account'))


# --- DELETE A SUBSCRIPTION (for debugging/cleanup) ---
@main.route('/subscriptions/<int:sub_id>', methods=['POST'])
@login_required
def delete_subscription(sub_id):
    """Deletes a specific push subscription."""
    # Use a simple CSRF form
    form = DeleteRestoreRequestForm()
    if form.validate_on_submit():
        try:
            sub = PushSubscription.query.get_or_404(sub_id)
            # Ensure the user owns this subscription
            if sub.user_id != current_user.id:
                 flash('Not authorized to delete this subscription.', 'danger')
                 return redirect(url_for('main.my_subscriptions'))

            db.session.delete(sub)
            db.session.commit()
            flash(f'Subscription {sub_id} deleted successfully.', 'success')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"ERROR deleting subscription {sub_id} for user {current_user.id}: {e}", exc_info=True)
            flash(f'Error deleting subscription: {e}', 'danger')
    else:
         flash('Invalid request to delete subscription (CSRF validation failed).', 'danger')

    return redirect(url_for('main.my_subscriptions')) # Redirect back to the list