# app/admin/routes.py
import csv
import io
import json
from flask import render_template, redirect, url_for, flash, request, current_app, abort
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from app import db
from app.admin import admin
# MODIFIED: Import RequestType
from app.models import User, Property, WorkOrder, Vendor, AuditLog, RequestType
from app.forms import (
    InviteUserForm, AddUserForm, AdminUpdateUserForm, AdminResetPasswordForm,
    PropertyForm, PropertyUploadForm, VendorForm, VendorUploadForm, ReassignRequestForm,
    # MODIFIED: Import RequestTypeForm
    RequestTypeForm
)
from app.decorators import admin_required, role_required
from app.email import send_notification_email
from app.extensions import db

@admin.route('/')
@admin_required
def admin_dashboard():
    return redirect(url_for('admin.manage_users'))

# --- USER MANAGEMENT ROUTES ---

@admin.route('/users', methods=['GET', 'POST'])
@admin_required
def manage_users():
    invite_form = InviteUserForm()
    add_user_form = AddUserForm()

    if invite_form.validate_on_submit() and 'invite_user' in request.form:
        user = User(
            name=invite_form.name.data,
            email=invite_form.email.data,
            role=invite_form.role.data,
            is_active=False
        )
        db.session.add(user)
        db.session.commit()

        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        token = s.dumps(user.email, salt='account-setup-salt')

        email_body = """
        <p>You have been invited to create an account for the BRC Vendor Form.</p>
        <p>Please click the link below to set your password and activate your account. This link will expire in 7 days.</p>
        """
        
        send_notification_email(
            subject="You're invited to the BRC Vendor Form",
            recipients=[user.email],
            text_body=f"You have been invited to create an account. Activate by visiting this link: {url_for('auth.set_password', token=token, _external=True)}",
            html_body=render_template(
                'email/notification_email.html',
                title="Account Invitation",
                user=user,
                body_content=email_body,
                link=url_for('auth.set_password', token=token, _external=True)
            )
        )
        flash(f'An invitation has been sent to {user.email}.', 'success')
        return redirect(url_for('admin.manage_users'))

    if add_user_form.validate_on_submit() and 'add_user' in request.form:
        if current_user.role == 'Super User':
            user = User(
                name=add_user_form.name.data,
                email=add_user_form.email.data,
                role=add_user_form.role.data,
                is_active=True
            )
            user.set_password(add_user_form.password.data)
            db.session.add(user)
            db.session.commit()
            flash(f'User {user.name} has been added and is now active.', 'success')
        else:
            flash('Only a Super User can add users directly.', 'danger')
        return redirect(url_for('admin.manage_users'))

    all_users = User.query.order_by(User.name).all()
    
    users_list = [
        {
            'id': user.id, 'name': user.name, 'email': user.email,
            'role': user.role, 'is_active': user.is_active
        } for user in all_users
    ]
    
    return render_template(
        'manage_users.html', title='User Management',
        invite_form=invite_form, add_user_form=add_user_form, 
        users_json=json.dumps(users_list)
    )

@admin.route('/user/<int:user_id>/resend-invite', methods=['POST'])
@admin_required
def resend_invitation(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_active:
        flash(f'User {user.name} is already active.', 'info')
        return redirect(url_for('admin.manage_users'))

    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    token = s.dumps(user.email, salt='account-setup-salt')

    email_body = """
    <p>You have been invited to create an account for the BRC Vendor Form.</p>
    <p>Please click the link below to set your password and activate your account. This link will expire in 7 days.</p>
    """
    
    send_notification_email(
        subject="Invitation to the BRC Vendor Form (Resent)",
        recipients=[user.email],
        text_body=f"Here is your new link to create an account: {url_for('auth.set_password', token=token, _external=True)}",
        html_body=render_template(
            'email/notification_email.html',
            title="Account Invitation",
            user=user,
            body_content=email_body,
            link=url_for('auth.set_password', token=token, _external=True)
        )
    )
    flash(f'A new invitation has been sent to {user.email}.', 'success')
    return redirect(url_for('admin.manage_users'))


@admin.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user_to_edit = User.query.get_or_404(user_id)
    # An Admin cannot edit another Admin or a Super User.
    if current_user.role == 'Admin' and user_to_edit.role in ['Admin', 'Super User']:
        flash('You do not have permission to edit this user.', 'danger')
        return redirect(url_for('admin.manage_users'))

    update_form = AdminUpdateUserForm(original_email=user_to_edit.email, obj=user_to_edit)
    password_form = AdminResetPasswordForm()

    # Super User can edit anyone, Admin can edit non-admins.
    if current_user.role == 'Super User':
        # Super User can see all roles in the dropdown
        update_form.role.choices = ['Requester', 'Scheduler', 'Property Manager', 'Admin', 'Super User']
    else: # This means current_user is an Admin
        # Admin cannot promote others to Admin or Super User
        update_form.role.choices = ['Requester', 'Scheduler', 'Property Manager']


    if 'update_user' in request.form and update_form.validate_on_submit():
        update_form.populate_obj(user_to_edit)
        db.session.commit()
        flash(f'User {user_to_edit.name} has been updated.', 'success')
        return redirect(url_for('admin.manage_users'))

    if 'reset_password' in request.form and password_form.validate_on_submit():
        user_to_edit.set_password(password_form.new_password.data)
        db.session.commit()
        flash(f"Password for {user_to_edit.name} has been reset.", "success")
        return redirect(url_for('admin.edit_user', user_id=user_id))

    return render_template(
        'edit_user.html', title='Edit User',
        update_form=update_form, password_form=password_form, user=user_to_edit
    )

@admin.route('/user/<int:user_id>/toggle-active', methods=['POST'])
@admin_required
def toggle_active_status(user_id):
    user = User.query.get_or_404(user_id)
    if user == current_user:
        flash('You cannot disable your own account.', 'danger')
        return redirect(url_for('admin.manage_users'))
    
    # Prevent Admin from disabling a Super User
    if current_user.role == 'Admin' and user.role == 'Super User':
        flash('Admins do not have permission to disable Super Users.', 'danger')
        return redirect(url_for('admin.manage_users'))

    user.is_active = not user.is_active
    db.session.commit()
    
    status = "enabled" if user.is_active else "disabled"
    flash(f"User {user.name} has been {status}.", 'success')
    return redirect(url_for('admin.manage_users'))

@admin.route('/user/<int:user_id>/delete', methods=['POST'])
@role_required('Super User')
def delete_user(user_id):
    user_to_delete = User.query.get_or_404(user_id)
    if user_to_delete == current_user:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin.manage_users'))

    # If the user is a Property Manager, find all WorkOrders they manage
    # and set the property_manager to None before deleting the user.
    if user_to_delete.role == 'Property Manager':
        WorkOrder.query.filter_by(property_manager=user_to_delete.name).update({"property_manager": None})
        db.session.commit()

    # Find all work orders associated with this user
    work_orders_to_disassociate = WorkOrder.query.filter_by(user_id=user_to_delete.id).all()
    for wo in work_orders_to_disassociate:
        # Add an audit log to trace the original requester
        log_text = f"Original requester '{user_to_delete.name}' has been deleted. The request is now unassigned."
        audit_log = AuditLog(text=log_text, user_id=current_user.id, work_order_id=wo.id)
        db.session.add(audit_log)
        # Disassociate the work order from the user
        wo.user_id = None
    
    db.session.commit()

    db.session.delete(user_to_delete)
    db.session.commit()
    flash(f'User {user_to_delete.name} has been deleted.', 'success')
    return redirect(url_for('admin.manage_users'))

@admin.route('/request/<int:request_id>/reassign', methods=['POST'])
@login_required
@admin_required
def reassign_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = ReassignRequestForm()
    if form.validate_on_submit():
        new_requester = form.requester.data
        work_order.user_id = new_requester.id
        work_order.requester_name = new_requester.name
        db.session.add(AuditLog(text=f'Request reassigned to {new_requester.name}', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash(f'Request has been reassigned to {new_requester.name}.', 'success')
    return redirect(url_for('main.view_request', request_id=request_id))


# --- PROPERTY MANAGEMENT ROUTES ---

@admin.route('/properties', methods=['GET', 'POST'])
@admin_required
def manage_properties():
    property_form = PropertyForm()
    upload_form = PropertyUploadForm()
    property_managers = User.query.filter_by(role='Property Manager').all()
    property_form.property_manager.choices = [("", "Select Manager...")] + [(pm.name, pm.name) for pm in property_managers]
    
    all_properties = Property.query.order_by(Property.name).all()
    
    properties_list = [
        {
            'id': prop.id, 'name': prop.name, 'address': prop.address,
            'property_manager': prop.property_manager
        } for prop in all_properties
    ]
    
    return render_template('manage_properties.html', title='Property Management',
                           property_form=property_form, upload_form=upload_form, 
                           properties_json=json.dumps(properties_list))

@admin.route('/upload_properties_csv', methods=['POST'])
@role_required('Super User')
def upload_properties_csv():
    upload_form = PropertyUploadForm()
    if upload_form.validate_on_submit():
        try:
            csv_file = upload_form.csv_file.data
            stream = io.StringIO(csv_file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.reader(stream)
            next(csv_reader, None)  # Skip header row

            updated_count = 0
            added_count = 0

            for row in csv_reader:
                if len(row) >= 2:
                    name = row[0].strip()
                    address = row[1].strip()
                    manager = row[2].strip() if len(row) > 2 else None
                    
                    prop = Property.query.filter_by(name=name).first()
                    if prop:
                        prop.address = address
                        prop.property_manager = manager
                        updated_count += 1
                    else:
                        new_prop = Property(name=name, address=address, property_manager=manager)
                        db.session.add(new_prop)
                        added_count += 1
            
            db.session.commit()
            flash(f'Properties successfully processed. Added: {added_count}, Updated: {updated_count}.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred during CSV upload: {e}', 'danger')
    else:
        flash('No file or an invalid file type was selected.', 'danger')
    
    return redirect(url_for('admin.manage_properties'))


@admin.route('/add_property', methods=['POST'])
@admin_required
def add_property():
    form = PropertyForm()
    property_managers = User.query.filter_by(role='Property Manager').all()
    form.property_manager.choices = [("", "Select Manager...")] + [(pm.name, pm.name) for pm in property_managers]

    if form.validate_on_submit():
        new_property = Property(
            name=form.name.data,
            address=form.address.data,
            property_manager=form.property_manager.data
        )
        db.session.add(new_property)
        try:
            db.session.commit()
            flash('Property added successfully.', 'success')
        except IntegrityError:
            db.session.rollback()
            flash('A property with this name already exists.', 'danger')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", 'danger')

    return redirect(url_for('admin.manage_properties'))


@admin.route('/edit_property/<int:property_id>', methods=['GET', 'POST'])
@admin_required
def edit_property(property_id):
    prop = Property.query.get_or_404(property_id)
    form = PropertyForm(obj=prop)
    property_managers = User.query.filter_by(role='Property Manager').all()
    form.property_manager.choices = [("", "Select Manager...")] + [(pm.name, pm.name) for pm in property_managers]

    if form.validate_on_submit():
        # Find all work orders associated with this property's ID before making changes.
        work_orders_to_update = WorkOrder.query.filter_by(property_id=prop.id).all()
        
        # Now, update the property object with the new form data.
        form.populate_obj(prop)
        
        # Loop through the found work orders and update them with the new property details.
        for wo in work_orders_to_update:
            wo.property = prop.name
            wo.address = prop.address
            wo.property_manager = prop.property_manager
            db.session.add(AuditLog(
                text=f"Property details updated automatically due to master property edit.",
                user_id=current_user.id,
                work_order_id=wo.id
            ))

        db.session.commit()
        flash('Property updated successfully. All associated work orders have been updated.', 'success')
        return redirect(url_for('admin.manage_properties'))
        
    return render_template('edit_property.html', title='Edit Property', form=form, property=prop)


@admin.route('/delete_property/<int:property_id>', methods=['POST'])
@role_required('Super User')
def delete_property(property_id):
    prop = Property.query.get_or_404(property_id)
    if WorkOrder.query.filter_by(property_id=prop.id).first():
        flash('Cannot delete property. It is associated with existing work orders.', 'danger')
        return redirect(url_for('admin.manage_properties'))

    db.session.delete(prop)
    db.session.commit()
    flash('Property has been deleted.', 'success')
    return redirect(url_for('admin.manage_properties'))

# --- VENDOR MANAGEMENT ROUTES ---

@admin.route('/vendors', methods=['GET', 'POST'])
@admin_required
def manage_vendors():
    vendor_form = VendorForm()
    upload_form = VendorUploadForm()
    
    all_vendors = Vendor.query.order_by(Vendor.company_name).all()
    
    vendors_list = [
        {
            'id': vendor.id,
            'company_name': vendor.company_name,
            'contact_name': vendor.contact_name,
            'email': vendor.email,
            'phone': vendor.phone,
            'specialty': vendor.specialty,
            'website': vendor.website
        }
        for vendor in all_vendors
    ]
    
    return render_template('manage_vendors.html', title='Vendor Management',
                           vendor_form=vendor_form, upload_form=upload_form, 
                           vendors_json=json.dumps(vendors_list))

@admin.route('/add_vendor', methods=['POST'])
@admin_required
def add_vendor():
    form = VendorForm()
    if form.validate_on_submit():
        # Check for existing vendor by email ONLY if an email is provided
        if form.email.data:
            existing_vendor_email = Vendor.query.filter_by(email=form.email.data).first()
            if existing_vendor_email:
                flash('A vendor with this email already exists.', 'danger')
                return redirect(url_for('admin.manage_vendors'))

        new_vendor = Vendor(
            company_name=form.company_name.data,
            contact_name=form.contact_name.data,
            email=form.email.data,
            phone=form.phone.data,
            specialty=form.specialty.data,
            website=form.website.data
        )
        db.session.add(new_vendor)
        try:
            db.session.commit()
            flash('Vendor added successfully.', 'success')
        except IntegrityError:
            db.session.rollback()
            # This will now only catch the duplicate company name error
            flash('A vendor with this company name already exists.', 'danger')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", 'danger')
    return redirect(url_for('admin.manage_vendors'))

@admin.route('/edit_vendor/<int:vendor_id>', methods=['GET', 'POST'])
@admin_required
def edit_vendor(vendor_id):
    vendor = Vendor.query.get_or_404(vendor_id)
    form = VendorForm(obj=vendor)
    if form.validate_on_submit():
        # Check for email conflict only if the email has changed and is not empty
        if form.email.data and form.email.data != vendor.email:
            existing_vendor = Vendor.query.filter_by(email=form.email.data).first()
            if existing_vendor:
                flash('That email is already in use by another vendor.', 'danger')
                return render_template('edit_vendor.html', title='Edit Vendor', form=form, vendor=vendor)
        
        form.populate_obj(vendor)
        db.session.commit()
        flash('Vendor updated successfully.', 'success')
        return redirect(url_for('admin.manage_vendors'))
    return render_template('edit_vendor.html', title='Edit Vendor', form=form, vendor=vendor)

@admin.route('/delete_vendor/<int:vendor_id>', methods=['POST'])
@role_required('Super User')
def delete_vendor(vendor_id):
    vendor = Vendor.query.get_or_404(vendor_id)
    if vendor.work_orders.first():
        flash('Cannot delete vendor. They are associated with existing work orders.', 'danger')
        return redirect(url_for('admin.manage_vendors'))
    if vendor.quotes.first():
        flash('Cannot delete vendor. They are associated with existing quotes.', 'danger')
        return redirect(url_for('admin.manage_vendors'))
    
    db.session.delete(vendor)
    db.session.commit()
    flash('Vendor has been deleted.', 'success')
    return redirect(url_for('admin.manage_vendors'))

@admin.route('/upload_vendors_csv', methods=['POST'])
@role_required('Super User')
def upload_vendors_csv():
    upload_form = VendorUploadForm()
    if upload_form.validate_on_submit():
        try:
            csv_file = upload_form.csv_file.data
            stream = io.StringIO(csv_file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.reader(stream)
            next(csv_reader, None) # Skip header row
            
            updated_count = 0
            added_count = 0

            for row in csv_reader:
                if len(row) >= 1:
                    company_name = row[0].strip()
                    contact_name = row[1].strip() if len(row) > 1 else None
                    email = row[2].strip() if len(row) > 2 else None
                    if email == '':
                        email = None
                    phone = row[3].strip() if len(row) > 3 else None
                    specialty = row[4].strip() if len(row) > 4 else None
                    website = row[5].strip() if len(row) > 5 else None
                    
                    # Skip if email is provided and already exists
                    if email and Vendor.query.filter_by(email=email).first():
                        continue

                    vendor = Vendor.query.filter_by(company_name=company_name).first()
                    if vendor:
                        vendor.contact_name = contact_name
                        vendor.email = email
                        vendor.phone = phone
                        vendor.specialty = specialty
                        vendor.website = website
                        updated_count += 1
                    else:
                        new_vendor = Vendor(
                            company_name=company_name,
                            contact_name=contact_name,
                            email=email,
                            phone=phone,
                            specialty=specialty,
                            website=website
                        )
                        db.session.add(new_vendor)
                        added_count += 1
            
            db.session.commit()
            flash(f'Vendors successfully processed. Added: {added_count}, Updated: {updated_count}.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred during CSV upload: {e}', 'danger')
    else:
        flash('No file or an invalid file type was selected.', 'danger')
        
    return redirect(url_for('admin.manage_vendors'))

# ADDED: Routes for managing request types
@admin.route('/request-types', methods=['GET', 'POST'])
@admin_required
def manage_request_types():
    form = RequestTypeForm()
    if form.validate_on_submit():
        request_type = RequestType(name=form.name.data)
        db.session.add(request_type)
        try:
            db.session.commit()
            flash('Request type added.', 'success')
        except IntegrityError:
            db.session.rollback()
            flash('That request type already exists.', 'danger')
        return redirect(url_for('admin.manage_request_types'))
    request_types = RequestType.query.order_by(RequestType.name).all()
    return render_template('manage_request_types.html', title='Manage Request Types', form=form, request_types=request_types)

@admin.route('/request-type/<int:request_type_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_request_type(request_type_id):
    request_type = RequestType.query.get_or_404(request_type_id)
    form = RequestTypeForm(obj=request_type)
    if form.validate_on_submit():
        request_type.name = form.name.data
        db.session.commit()
        flash('Request type updated.', 'success')
        return redirect(url_for('admin.manage_request_types'))
    return render_template('edit_request_type.html', title='Edit Request Type', form=form, request_type=request_type)

@admin.route('/request-type/<int:request_type_id>/delete', methods=['POST'])
@admin_required
def delete_request_type(request_type_id):
    request_type = RequestType.query.get_or_404(request_type_id)
    if request_type.work_orders.first():
        flash('This request type is in use and cannot be deleted.', 'danger')
    else:
        db.session.delete(request_type)
        db.session.commit()
        flash('Request type deleted.', 'success')
    return redirect(url_for('admin.manage_request_types'))