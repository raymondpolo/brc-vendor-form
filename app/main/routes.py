# app/main/routes.py
import os
import csv
import io
import re
import json
import uuid
from functools import wraps
from collections import Counter
from datetime import datetime, time, timedelta

from flask import (render_template, request, redirect, url_for, flash,
                   abort, send_from_directory, jsonify, current_app, Response)
from flask_login import login_required, current_user
from sqlalchemy import or_, func, case
import bleach

from app import db, csrf
from app.main import main
from app.models import (User, WorkOrder, Property, Note, Notification,
                        AuditLog, Attachment, Vendor, Quote)
from app.forms import (NoteForm, ChangeStatusForm, AttachmentForm, NewRequestForm,
                       UpdateAccountForm, ChangePasswordForm, AssignVendorForm, ReportForm, QuoteForm)
from app.email import send_notification_email
from werkzeug.utils import secure_filename
from app.decorators import admin_required

request_types_list = [
    'Appliance', 'Junk Removal', 'Plumbing', 'Pest Control', 'Electrical',
    'Painting', 'Cleaning', 'Fence', 'Power Wash', 'Flooring', 'Window'
]

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
    return {
        'id': req.id,
        'date_created': req.date_created.strftime('%Y-%m-%d'),
        'wo_number': req.wo_number,
        'requester_name': req.requester_name,
        'property': req.property,
        'status': req.status,
        'request_type': req.request_type,
        'tag': req.tag
    }

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

    stats_query = db.session.query(
        func.count(WorkOrder.id).label('total'),
        func.sum(case((WorkOrder.status == 'New', 1), else_=0)).label('new'),
        func.sum(case((WorkOrder.status == 'Open', 1), else_=0)).label('open'),
        func.sum(case((WorkOrder.status == 'Pending', 1), else_=0)).label('pending'),
        func.sum(case((WorkOrder.status == 'Scheduled', 1), else_=0)).label('scheduled'),
        func.sum(case((WorkOrder.status == 'Cancelled', 1), else_=0)).label('cancelled'),
        func.sum(case((WorkOrder.status == 'Closed', 1), else_=0)).label('closed')
    ).one()
    stats = {
        "totalRequests": stats_query.total or 0, "new": stats_query.new or 0,
        "open": stats_query.open or 0, "pending": stats_query.pending or 0,
        "scheduled": stats_query.scheduled or 0, "cancelled": stats_query.cancelled or 0,
        "closed": stats_query.closed or 0,
    }

    all_work_orders = WorkOrder.query.all()
    all_tags = []
    for wo in all_work_orders:
        if wo.tag:
            all_tags.extend(filter(None, wo.tag.split(',')))
    tag_counts = Counter(all_tags)
    
    tag_stats = {
        "approved": tag_counts.get('Approved', 0),
        "declined": tag_counts.get('Declined', 0),
        "waiting_approval": tag_counts.get('Waiting Approval', 0),
        "follow_up": tag_counts.get('Follow-up needed', 0),
        "go_back": tag_counts.get('Go-back', 0)
    }

    status_counts = Counter(req.status for req in all_work_orders)
    type_counts = Counter(req.request_type for req in all_work_orders)
    property_counts = Counter(req.property for req in all_work_orders)
    vendor_counts = Counter(req.vendor.company_name for req in all_work_orders if req.vendor)
    
    approved_by_pm = Counter(wo.property_manager for wo in all_work_orders if wo.tag and 'Approved' in wo.tag.split(','))
    declined_by_pm = Counter(wo.property_manager for wo in all_work_orders if wo.tag and 'Declined' in wo.tag.split(','))
    
    goback_work_orders = WorkOrder.query.filter(WorkOrder.tag.like('%Go-back%')).all()
    goback_by_vendor = Counter(wo.vendor.company_name if wo.vendor else 'Unassigned' for wo in goback_work_orders)

    chart_data = {
        "status": {"labels": list(status_counts.keys()), "data": list(status_counts.values())},
        "type": {"labels": list(type_counts.keys()), "data": list(type_counts.values())},
        "property": {"labels": list(property_counts.keys()), "data": list(property_counts.values())},
        "vendor": {"labels": list(vendor_counts.keys()), "data": list(vendor_counts.values())},
        "approved_by_pm": {"labels": list(approved_by_pm.keys()), "data": list(approved_by_pm.values())},
        "declined_by_pm": {"labels": list(declined_by_pm.keys()), "data": list(declined_by_pm.values())},
        "goback_by_vendor": {"labels": list(goback_by_vendor.keys()), "data": list(goback_by_vendor.values())}
    }

    return render_template(
        'dashboard.html', title='Dashboard', stats=stats,
        tag_stats=tag_stats, chart_data=chart_data
    )

@main.route('/requests')
@login_required
@admin_required
def all_requests():
    requests_data = WorkOrder.query.order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in requests_data]
    return render_template('all_requests.html', title='All Requests', 
                           requests_json=json.dumps(requests_list))

@main.route('/my-requests')
@login_required
def my_requests():
    query = WorkOrder.query
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
    query = WorkOrder.query.filter(WorkOrder.viewers.contains(current_user))
    requests_data = query.order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in requests_data]
    return render_template('shared_requests.html', title='Shared With Me', 
                           requests_json=json.dumps(requests_list))

@main.route('/requests/status/<status>')
@login_required
@admin_required
def requests_by_status(status):
    filtered_requests = WorkOrder.query.filter_by(status=status).order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in filtered_requests]
    return render_template('requests_by_status.html', title=f'Requests: {status}', 
                           requests_json=json.dumps(requests_list), status=status)

@main.route('/requests/tag/<tag_name>')
@login_required
@admin_required
def requests_by_tag(tag_name):
    tagged_requests = WorkOrder.query.filter(WorkOrder.tag.like(f'%{tag_name}%')).order_by(WorkOrder.date_created.desc()).all()
    requests_list = [work_order_to_dict(req) for req in tagged_requests]
    return render_template('requests_by_tag.html', title=f'Requests Tagged: {tag_name}', 
                           requests_json=json.dumps(requests_list), tag_name=tag_name)

@main.route('/request/<int:request_id>', methods=['GET', 'POST'])
@login_required
def view_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']
    if not (is_author or is_viewer or is_property_manager or is_admin_staff):
        abort(403)

    note_form = NoteForm()
    status_form = ChangeStatusForm()
    attachment_form = AttachmentForm()
    assign_vendor_form = AssignVendorForm()
    quote_form = QuoteForm()
    requester_initials = get_requester_initials(work_order.requester_name)
    quotes = work_order.quotes

    if is_admin_staff:
        status_form.status.choices = [c for c in status_form.status.choices if c[0] not in ['Approved', 'Quote Declined', 'New']]
    
    if request.method == 'GET':
        if is_admin_staff and work_order.status == 'New':
            work_order.status = 'Open'
            db.session.add(AuditLog(text='Status changed to Open.', user_id=current_user.id, work_order_id=work_order.id))
            flash('Request status has been updated to Open.', 'info')
        db.session.add(AuditLog(text='Viewed the request.', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()

    if note_form.validate_on_submit() and 'post_note' in request.form:
        note_text = note_form.text.data
        note = Note(text=note_text, author=current_user, work_order=work_order)
        db.session.add(note)
        notified_users = {work_order.author} if work_order.author != current_user else set()
        
        tagged_names = re.findall(r'@(\w+(?:\s\w+)?)', note_text)
        for name in tagged_names:
            tagged_user = User.query.filter(User.name.ilike(name.strip())).first()
            if tagged_user:
                if tagged_user not in work_order.viewers:
                    work_order.viewers.append(tagged_user)
                if tagged_user != current_user:
                    notified_users.add(tagged_user)
        db.session.commit()

        for user in notified_users:
            notification = Notification(text=f'{current_user.name} mentioned you in a note on Request #{work_order.id}',
                link=url_for('main.view_request', request_id=work_order.id), user_id=user.id)
            db.session.add(notification)
            email_body = f"""
            <p><b>{current_user.name}</b> mentioned you in a note on Request #{work_order.id} for property <b>{work_order.property}</b>.</p>
            <p><b>Note:</b></p>
            <p style="padding-left: 20px; border-left: 3px solid #eee;">{note.text}</p>
            """
            send_notification_email(
                subject=f"New Note on Request #{work_order.id}",
                recipients=[user.email],
                text_body=f"{current_user.name} mentioned you in a note on Request #{work_order.id}",
                html_body=render_template(
                    'email/notification_email.html',
                    title="New Note on Request",
                    user=user,
                    body_content=email_body,
                    link=url_for('main.view_request', request_id=work_order.id, _external=True)
                )
            )
        db.session.commit()
        flash('Your note has been added.', 'success')
        return redirect(url_for('main.view_request', request_id=work_order.id))

    notes = Note.query.filter_by(work_order_id=request_id).order_by(Note.date_posted.asc()).all()
    audit_logs = AuditLog.query.filter_by(work_order_id=request_id).order_by(AuditLog.timestamp.asc()).all()
    return render_template('view_request.html', title=f'Request #{work_order.id}', work_order=work_order, notes=notes,
                           note_form=note_form, status_form=status_form, audit_logs=audit_logs,
                           attachment_form=attachment_form, assign_vendor_form=assign_vendor_form,
                           requester_initials=requester_initials, quote_form=quote_form, quotes=quotes)


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
            return redirect(url_for('main.view_request', request_id=request_id))

        work_order.status = new_status
        log_text = f'Changed status from {old_status} to {new_status}.'
        
        if new_status == 'Scheduled':
            work_order.scheduled_date = datetime.strptime(form.scheduled_date.data, '%m/%d/%Y').date()
            log_text += f' for {work_order.scheduled_date.strftime("%Y-%m-%d")}'
        else:
            work_order.scheduled_date = None
            
        current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])
        if new_status == 'Closed':
            current_tags.add('Completed')
            work_order.date_completed = datetime.utcnow()
            log_text += " and tagged as 'Completed'."
        
        if old_status == 'Closed' and new_status != 'Closed':
            current_tags.discard('Completed')

        work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
        db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))

        if work_order.author != current_user:
            notification = Notification(text=f'Status for Request #{work_order.id} changed to {new_status}.',
                link=url_for('main.view_request', request_id=work_order.id), user_id=work_order.user_id)
            db.session.add(notification)
            email_body = f"<p>The status of your Request #{work_order.id} for property <b>{work_order.property}</b> was changed from <b>{old_status}</b> to <b>{new_status}</b>.</p>"
            send_notification_email(
                subject=f"Status Update for Request #{work_order.id}",
                recipients=[work_order.author.email],
                text_body=f"The status of your Request #{work_order.id} was changed to {new_status}.",
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
                manager_notification = Notification(
                    text=f'A quote has been sent for Request #{work_order.id} at {work_order.property}.',
                    link=url_for('main.view_request', request_id=work_order.id),
                    user_id=manager.id)
                db.session.add(manager_notification)
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
        flash('Could not update status. Please check the form for errors.', 'danger')

    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/tag_request/<int:request_id>', methods=['POST'])
@login_required
def tag_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    action = request.form.get('action')
    tag_value = request.form.get('tag')
    
    can_pm = current_user.role == 'Property Manager' and current_user.name == work_order.property_manager
    can_requester = current_user.id == work_order.user_id
    can_admin = current_user.role in ['Admin', 'Scheduler', 'Super User']

    current_tags = set(work_order.tag.split(',') if work_order.tag and work_order.tag.strip() else [])
    log_text = ""

    if action == 'remove_tag':
        if not can_admin:
            abort(403)
        tag_to_remove = request.form.get('tag_to_remove')
        if tag_to_remove:
            current_tags.discard(tag_to_remove)
            log_text = f"Tag '{tag_to_remove}' removed"
            flash(f"Tag '{tag_to_remove}' has been removed.", 'info')
    else:
        tag_to_add = tag_value
        log_text = f"Request tagged as '{tag_to_add}'"
        
        if tag_to_add in ['Approved', 'Declined']:
            if not can_pm: abort(403)
            current_tags.discard('Approved')
            current_tags.discard('Declined')
            current_tags.discard('Waiting Approval')
            current_tags.add(tag_to_add)
            flash(f'Quote has been {tag_to_add.lower()}.', 'success')
        
        elif tag_to_add == 'Waiting Approval':
            if not (can_pm or can_admin): abort(403)
            work_order.status = 'Pending'
            current_tags.add(tag_to_add)
            log_text += " and status set to 'Pending'"
            flash('Request status set to Pending, tagged for approval.', 'info')

        elif tag_to_add == 'Follow-up needed':
            if not can_admin: abort(403)
            work_order.status = 'Pending'
            current_tags.add(tag_to_add)
            log_text += " and status set to 'Pending'"
            flash('Request status set to Pending, tagged for follow-up.', 'info')

        elif tag_to_add == 'Completed':
            if not (can_pm or can_requester or can_admin): abort(403)
            current_tags.add('Completed')
            work_order.status = 'Closed'
            work_order.date_completed = datetime.utcnow()
            log_text = "Request marked as 'Completed' and status set to 'Closed'"
            flash('Request has been marked as completed.', 'success')
        
        elif tag_to_add == 'Go-back':
            if not (can_pm or can_admin or can_requester): abort(403)
            work_order.status = 'Open'
            current_tags.discard('Completed')
            current_tags.add('Go-back')
            log_text = "Request has been reopened (Go-back)"
            flash('Request has been reopened.', 'info')
        
        else:
            flash('Invalid action.', 'danger')
            return redirect(url_for('main.view_request', request_id=request_id))

    work_order.tag = ','.join(sorted(list(filter(None, current_tags)))) if current_tags else None
    db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))
    db.session.commit()
    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/cancel_request/<int:request_id>', methods=['POST'])
@login_required
def cancel_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    is_author = work_order.author == current_user
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    if not (is_author or is_property_manager):
        abort(403)
    if work_order.status in ['Closed', 'Cancelled']:
        flash('This request cannot be cancelled as it is already closed.', 'warning')
        return redirect(url_for('main.view_request', request_id=request_id))
    work_order.status = 'Cancelled'
    work_order.tag = None
    log_text = f'Request cancelled by {current_user.name} ({current_user.role})'
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
    db.session.add(AuditLog(text=f"Vendor '{vendor.company_name}' assigned.", user_id=current_user.id, work_order_id=work_order.id))
    db.session.commit()
    flash(f"Vendor '{vendor.company_name}' has been assigned to this request.", 'success')
    return redirect(url_for('main.view_request', request_id=request_id))

@main.route('/unassign_vendor/<int:request_id>', methods=['POST'])
@login_required
@admin_required
def unassign_vendor(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    if work_order.vendor:
        vendor_name = work_order.vendor.company_name
        work_order.vendor_id = None
        db.session.add(AuditLog(text=f"Vendor '{vendor_name}' unassigned.", user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash(f"Vendor '{vendor_name}' has been unassigned.", 'success')
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
    properties = Property.query.all()
    properties_dict = {p.name: {"address": p.address, "manager": p.property_manager} for p in properties}
    form = NewRequestForm()
    if form.validate_on_submit():
        date1 = datetime.strptime(form.date_1.data, '%m/%d/%Y').date() if form.date_1.data else None
        date2 = datetime.strptime(form.date_2.data, '%m/%d/%Y').date() if form.date_2.data else None
        date3 = datetime.strptime(form.date_3.data, '%m/%d/%Y').date() if form.date_3.data else None

        new_order = WorkOrder(
            wo_number=form.wo_number.data, requester_name=current_user.name,
            request_type=form.request_type.data, description=form.description.data,
            property=form.property.data, unit=form.unit.data,
            address=properties_dict.get(form.property.data, {}).get('address', ''),
            property_manager=properties_dict.get(form.property.data, {}).get('manager', ''),
            tenant_name=form.tenant_name.data, tenant_phone=form.tenant_phone.data,
            contact_person=form.contact_person.data, contact_person_phone=form.contact_person_phone.data,
            preferred_date_1=date1, preferred_date_2=date2,
            preferred_date_3=date3, user_id=current_user.id)
        
        if form.vendor_assigned.data:
            vendor = Vendor.query.filter(Vendor.company_name.ilike(form.vendor_assigned.data)).first()
            if vendor:
                new_order.vendor_id = vendor.id

        db.session.add(new_order)
        db.session.commit()

        for file in form.attachments.data:
            save_attachment(file, new_order.id)
            
        admins_and_schedulers = User.query.filter(User.role.in_(['Admin', 'Scheduler', 'Super User'])).all()
        for user in admins_and_schedulers:
            if user != current_user:
                notification = Notification(text=f'New request #{new_order.id} submitted by {current_user.name}.',
                    link=url_for('main.view_request', request_id=new_order.id), user_id=user.id)
                db.session.add(notification)
        db.session.commit()
        flash('Your request has been created!', 'success')
        return redirect(url_for('main.my_requests'))
    
    elif request.method == 'POST':
        print("--- FORM VALIDATION FAILED ---")
        print("Errors:", form.errors)
        print("----------------------------")
        
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

    if is_author and work_order.status in ['Closed', 'Cancelled']:
        flash('This request cannot be edited because it is already closed.', 'warning')
        return redirect(url_for('main.view_request', request_id=work_order.id))
        
    properties = Property.query.all()
    properties_dict = {p.name: {"address": p.address, "manager": p.property_manager} for p in properties}
    form = NewRequestForm(obj=work_order)
    
    del form.attachments

    if form.validate_on_submit():
        work_order.wo_number = form.wo_number.data
        work_order.request_type = form.request_type.data
        work_order.description = form.description.data
        work_order.property = form.property.data
        work_order.unit = form.unit.data
        work_order.tenant_name = form.tenant_name.data
        work_order.tenant_phone = form.tenant_phone.data
        work_order.contact_person = form.contact_person.data
        work_order.contact_person_phone = form.contact_person_phone.data
        
        work_order.preferred_date_1 = datetime.strptime(form.date_1.data, '%m/%d/%Y').date() if form.date_1.data else None
        work_order.preferred_date_2 = datetime.strptime(form.date_2.data, '%m/%d/%Y').date() if form.date_2.data else None
        work_order.preferred_date_3 = datetime.strptime(form.date_3.data, '%m/%d/%Y').date() if form.date_3.data else None
        
        if 'attachments' in request.files:
            for file in request.files.getlist('attachments'):
                if file.filename:
                    save_attachment(file, work_order.id)

        db.session.add(AuditLog(text='Edited request details.', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash('Request has been updated.', 'success')
        return redirect(url_for('main.view_request', request_id=work_order.id))
        
    elif request.method == 'POST':
        print("--- EDIT FORM VALIDATION FAILED ---")
        print("Errors:", form.errors)
        print("---------------------------------")

    if request.method == 'GET':
        form.date_1.data = work_order.preferred_date_1.strftime('%m/%d/%Y') if work_order.preferred_date_1 else ''
        form.date_2.data = work_order.preferred_date_2.strftime('%m/%d/%Y') if work_order.preferred_date_2 else ''
        form.date_3.data = work_order.preferred_date_3.strftime('%m/%d/%Y') if work_order.preferred_date_3 else ''
    
    return render_template('edit_request.html', title='Edit Request', form=form, work_order=work_order,
                           properties=properties, property_data=json.dumps(properties_dict))

@main.route('/upload_attachment/<int:request_id>', methods=['POST'])
@login_required
def upload_attachment(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = AttachmentForm()
    if form.validate_on_submit():
        for file in form.file.data:
            if file and file.filename:
                file_type = request.form.get('file_type', 'Attachment')
                attachment_obj = save_attachment(file, request_id, file_type)
                
                if attachment_obj:
                    db.session.add(AuditLog(text=f'Uploaded {file_type}: {secure_filename(file.filename)}', user_id=current_user.id, work_order_id=work_order.id))
                    flash(f'{file_type} "{secure_filename(file.filename)}" uploaded successfully.', 'success')
                else:
                    flash('There was an error uploading one of the files.', 'danger')
            else:
                flash('No file selected or file was empty.', 'danger')
        db.session.commit()
    else:
        flash('File upload failed validation.', 'danger')
    return redirect(url_for('main.view_request', request_id=request_id))


@main.route('/download_attachment/<int:attachment_id>')
@login_required
def download_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    work_order = WorkOrder.query.get_or_404(attachment.work_order_id)
    
    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']

    if not (is_author or is_viewer or is_property_manager or is_admin_staff):
        abort(403)
        
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], attachment.filename, as_attachment=True)

@main.route('/view_attachment/<int:attachment_id>')
@login_required
def view_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    work_order = WorkOrder.query.get_or_404(attachment.work_order_id)
    
    is_author = work_order.author == current_user
    is_viewer = current_user in work_order.viewers
    is_property_manager = current_user.role == 'Property Manager' and work_order.property_manager == current_user.name
    is_admin_staff = current_user.role in ['Admin', 'Scheduler', 'Super User']
    
    if not (is_author or is_viewer or is_property_manager or is_admin_staff):
        abort(403)
        
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], attachment.filename, as_attachment=False)

@main.route('/delete_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    if attachment.user_id != current_user.id and current_user.role not in ['Admin', 'Super User']:
        abort(403)
    try:
        os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], attachment.filename))
    except OSError:
        pass
    db.session.add(AuditLog(text=f'Deleted attachment: {attachment.filename}', user_id=current_user.id, work_order_id=attachment.work_order_id))
    db.session.delete(attachment)
    db.session.commit()
    flash('Attachment deleted.', 'success')
    return redirect(url_for('main.view_request', request_id=attachment.work_order_id))

@main.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    update_form = UpdateAccountForm(obj=current_user)
    password_form = ChangePasswordForm()
    if 'update_account' in request.form and update_form.validate_on_submit():
        current_user.name = update_form.name.data
        current_user.email = update_form.email.data
        
        if current_user.role in ['Admin', 'Scheduler', 'Super User', 'Property Manager']:
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
            
            signature_html = request.form.get('signature')
            clean_html = bleach.clean(
                signature_html,
                tags=allowed_tags,
                attributes=allowed_attrs,
                protocols=['http', 'https', 'mailto', 'data']
            )
            
            current_user.signature = clean_html
        
        db.session.commit()
        flash('Your account has been updated!', 'success')
        return redirect(url_for('main.account'))
        
    if 'change_password' in request.form and password_form.validate_on_submit():
        if current_user.check_password(password_form.current_password.data):
            current_user.set_password(password_form.new_password.data)
            db.session.commit()
            flash('Your password has been changed!', 'success')
        else:
            flash('Incorrect current password.', 'danger')
        return redirect(url_for('main.account'))

    return render_template('account.html', title='Account', update_form=update_form, password_form=password_form)


@main.route('/upload_image', methods=['POST'])
@login_required
def upload_image():
    if 'upload' in request.files:
        file = request.files['upload']
        if file:
            filename = secure_filename(file.filename)
            ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
            unique_filename = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename))
            url = url_for('main.uploaded_file', filename=unique_filename, _external=True)
            return jsonify({'uploaded': 1, 'fileName': unique_filename, 'url': url})
    return jsonify({'uploaded': 0, 'error': {'message': 'Upload failed'}})

@main.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
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
    
    start_date, end_date = get_date_range(request.args.get('date_range'), start_date_str, end_date_str)
    
    date_column = getattr(WorkOrder, date_type)
    if start_date:
        query = query.filter(date_column >= start_date)
    if end_date:
        query = query.filter(date_column <= end_date)

    work_orders = query.order_by(WorkOrder.date_created.asc()).all()
    
    string_io = io.StringIO()
    csv_writer = csv.writer(string_io)
    headers = [
        'ID', 'WO Number', 'Status', 'Tag', 'Vendor Assigned', 'Date Created', 'Date Completed', 'Requester', 'Request Type',
        'Property', 'Unit', 'Address', 'Description'
    ]
    csv_writer.writerow(headers)
    for wo in work_orders:
        csv_writer.writerow([
            wo.id, wo.wo_number, wo.status, wo.tag, wo.vendor.company_name if wo.vendor else '', 
            wo.date_created.strftime('%Y-%m-%d %H:%M'),
            wo.date_completed.strftime('%Y-%m-%d %H:%M') if wo.date_completed else '',
            wo.requester_name, wo.request_type, wo.property, wo.unit,
            wo.address, wo.description
        ])
    
    output = string_io.getvalue()
    string_io.close()
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=work_orders_{date_type}.csv"}
    )

@main.route('/reports/download/summary')
@login_required
@admin_required
def download_summary_report():
    date_type = request.args.get('date_type', 'date_created')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    start_date, end_date = get_date_range(request.args.get('date_range'), start_date_str, end_date_str)
    
    base_query = WorkOrder.query
    date_column = getattr(WorkOrder, date_type)

    if start_date:
        base_query = base_query.filter(date_column >= start_date)
    if end_date:
        base_query = base_query.filter(date_column <= end_date)

    filtered_orders = base_query.all()

    status_counts = Counter(req.status for req in filtered_orders)
    type_counts = Counter(req.request_type for req in filtered_orders)
    property_counts = Counter(req.property for req in filtered_orders)
    
    string_io = io.StringIO()
    csv_writer = csv.writer(string_io)
    csv_writer.writerow(['Summary by Status'])
    csv_writer.writerow(['Status', 'Count'])
    for status, count in status_counts.items():
        csv_writer.writerow([status, count])
    csv_writer.writerow([])
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
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"summary_report_{date_type}.csv"}
    )

@main.route('/calendar')
@login_required
def calendar():
    return render_template('calendar.html', title='Calendar')

@main.route('/api/events')
@login_required
def api_events():
    query = WorkOrder.query.filter(WorkOrder.scheduled_date.isnot(None))
    if current_user.role == 'Requester':
        query = query.filter(WorkOrder.user_id == current_user.id)
    events = query.all()
    event_list = [{'title': f"Request #{event.id}",
                   'start': event.scheduled_date.strftime('%Y-%m-%d'),
                   'url': url_for('main.view_request', request_id=event.id)}
                  for event in events]
    return jsonify(event_list)
    
@main.route('/api/vendors/search')
@login_required
def search_vendors():
    q = request.args.get('q')
    if q:
        vendors = Vendor.query.filter(Vendor.company_name.ilike(f'%{q}%')).all()
        return jsonify([{'id': v.id, 'company_name': v.company_name, 'contact_name': v.contact_name, 'email': v.email, 'phone': v.phone, 'specialty': v.specialty, 'website': v.website} for v in vendors])
    return jsonify([])

@main.route('/api/users/search')
@login_required
def api_user_search():
    users = User.query.filter_by(is_active=True).all()
    user_list = [{'key': user.name, 'value': user.name.replace(' ', '')} for user in users]
    return jsonify(user_list)

@main.route('/request/<int:request_id>/send_email', methods=['POST'])
@login_required
@admin_required
def send_work_order_email(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    data = request.get_json()
    recipient = data.get('recipient')
    cc = data.get('cc')
    subject = data.get('subject')
    body = data.get('body')

    if not recipient:
        return jsonify({'success': False, 'message': 'Recipient email is required.'}), 400

    recipients = [recipient]
    cc_list = [email.strip() for email in cc.split(',')] if cc else []

    # Create a plain text version of the body by stripping all HTML tags
    text_version_of_body = bleach.clean(body, tags=[], strip=True).strip()

    text_body = render_template('email/work_order_email.txt', work_order=work_order, body=text_version_of_body)
    html_body = render_template('email/work_order_email.html', work_order=work_order, body=body)

    send_notification_email(
        subject=subject,
        recipients=recipients,
        cc=cc_list,
        text_body=text_body,
        html_body=html_body
    )
    
    db.session.add(AuditLog(text=f"Work order emailed to {recipient}", user_id=current_user.id, work_order_id=work_order.id))
    db.session.commit()

    return jsonify({'success': True, 'message': 'Email sent successfully!'})

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
            quote = Quote(
                work_order_id=work_order.id,
                vendor_id=vendor.id,
                attachment_id=attachment_obj.id
            )
            db.session.add(quote)
            db.session.add(AuditLog(text=f"Quote '{attachment_obj.filename}' for vendor '{vendor.company_name}' uploaded.", user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash(f'Quote for {vendor.company_name} uploaded successfully.', 'success')
        else:
            flash('There was an error saving the quote file.', 'danger')
    else:
        flash('There was an error with the quote form. Please select a vendor and a valid file.', 'danger')
    return redirect(url_for('main.view_request', request_id=request_id))

def get_date_range(range_name, start_str, end_str):
    today = datetime.utcnow().date()
    start_date, end_date = None, None

    if range_name == 'today':
        start_date = today
        end_date = today
    elif range_name == 'yesterday':
        start_date = today - timedelta(days=1)
        end_date = start_date
    elif range_name == 'this_week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif range_name == 'last_week':
        end_of_last_week = today - timedelta(days=today.weekday() + 1)
        start_date = end_of_last_week - timedelta(days=6)
        end_date = end_of_last_week
    elif range_name == 'this_month':
        start_date = today.replace(day=1)
        next_month = start_date.replace(day=28) + timedelta(days=4)
        end_date = next_month - timedelta(days=next_month.day)
    elif range_name == 'last_month':
        end_of_last_month = today.replace(day=1) - timedelta(days=1)
        start_date = end_of_last_month.replace(day=1)
        end_date = end_of_last_month
    elif range_name == 'this_year':
        start_date = today.replace(month=1, day=1)
        end_date = today.replace(month=12, day=31)
    elif range_name == 'last_year':
        last_year = today.year - 1
        start_date = datetime(last_year, 1, 1).date()
        end_date = datetime(last_year, 12, 31).date()
    elif range_name == 'custom_date':
        if start_str:
            start_date = datetime.strptime(start_str, '%m/%d/%Y').date()
            end_date = start_date
    elif range_name == 'custom_range':
        if start_str:
            start_date = datetime.strptime(start_str, '%m/%d/%Y').date()
        if end_str:
            end_date = datetime.strptime(end_str, '%m/%d/%Y').date()

    if start_date:
        start_date = datetime.combine(start_date, time.min)
    if end_date:
        end_date = datetime.combine(end_date, time.max)
        
    return start_date, end_date