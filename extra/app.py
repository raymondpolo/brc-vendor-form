# app.py
# Main Python file for our Flask application.
# --- INSTRUCTIONS ---
# 1. Install required packages:
#    pip install Flask Flask-SQLAlchemy Flask-Login Flask-WTF Werkzeug email_validator Flask-Mail itsdangerous
#
# 2. Save this file as `app.py`.
#
# 3. Create a folder named `templates` in the same directory.
#
# 4. Create a folder named `static` in the same directory.
#    - Inside `static`, save your logo as `logo.png`.
#    - Inside `static`, create another folder named `uploads`.
#
# 5. Save all the HTML files below into the `templates` folder.
#
# 6. Delete any old `site.db` files from your project folder.
#
# 7. Configure your email server settings in the "App Configuration" section below.
#
# 8. Run this file to start the server: `python app.py`

import os
import csv
import io
import re
from flask import Flask, render_template, request, redirect, url_for, flash, abort, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, SelectField, TextAreaField, DateField, MultipleFileField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError, Optional
from flask_wtf.file import FileField, FileAllowed, FileRequired
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired
import json
from datetime import datetime
from collections import Counter
from functools import wraps
from sqlalchemy import or_

# --- App Configuration ---
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-very-secret-key-that-should-be-changed'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'site.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True) # Create the uploads folder if it doesn't exist

# --- Email Configuration (for password reset) ---
# Replace with your own email server details.
# For Gmail, you might need to use an "App Password".
app.config['MAIL_SERVER'] = 'smtp.googlemail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('EMAIL_USER') # Set as environment variable
app.config['MAIL_PASSWORD'] = os.environ.get('EMAIL_PASS') # Set as environment variable
mail = Mail(app)
s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# --- Database Models ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    role = db.Column(db.String(20), nullable=False, default='Requester')
    requests = db.relationship('WorkOrder', backref='author', lazy=True)
    notes = db.relationship('Note', backref='author', lazy=True)
    notifications = db.relationship('Notification', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class WorkOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wo_number = db.Column(db.String(100), nullable=False)
    requester_name = db.Column(db.String(100), nullable=False)
    request_type = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    property = db.Column(db.String(100), nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    address = db.Column(db.String(200), nullable=False)
    property_manager = db.Column(db.String(100), nullable=True)
    tenant_name = db.Column(db.String(100), nullable=True)
    tenant_phone = db.Column(db.String(20), nullable=True)
    contact_person = db.Column(db.String(100), nullable=True)
    contact_person_phone = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='New')
    date_created = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    scheduled_date = db.Column(db.Date, nullable=True)
    preferred_date_1 = db.Column(db.Date, nullable=True)
    preferred_date_2 = db.Column(db.Date, nullable=True)
    preferred_date_3 = db.Column(db.Date, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    notes = db.relationship('Note', backref='work_order', lazy=True, cascade="all, delete-orphan")
    audit_logs = db.relationship('AuditLog', backref='work_order', lazy=True, cascade="all, delete-orphan")
    attachments = db.relationship('Attachment', backref='work_order', lazy=True, cascade="all, delete-orphan")

class Property(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    address = db.Column(db.String(200), nullable=False)
    property_manager = db.Column(db.String(100), nullable=True)

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=False)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=False)
    user = db.relationship('User')

class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=False)
    user = db.relationship('User')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Decorator for role-based access ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['Admin', 'Super User']:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

# --- Static Data ---
request_types_list = [
    'Appliance', 'Junk Removal', 'Plumbing', 'Pest Control', 'Electrical',
    'Painting', 'Cleaning', 'Fence', 'Power Wash', 'Flooring', 'Window'
]

# --- Forms ---
class RegistrationForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    role = SelectField('Role', choices=['Requester', 'Scheduler', 'Property Manager'], validators=[DataRequired()])
    submit = SubmitField('Sign Up')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('That email is taken. Please choose a different one.')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')

class PropertyUploadForm(FlaskForm):
    csv_file = FileField('Properties CSV File', validators=[FileAllowed(['csv'])])
    submit = SubmitField('Upload')

class UpdateAccountForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Update Account')

    def validate_email(self, email):
        if email.data != current_user.email:
            user = User.query.filter_by(email=email.data).first()
            if user:
                raise ValidationError('That email is already in use. Please choose a different one.')

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Change Password')

class AdminUpdateUserForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    role = SelectField('Role', choices=['Requester', 'Scheduler', 'Property Manager', 'Admin', 'Super User'], validators=[DataRequired()])
    submit = SubmitField('Update User')

    def __init__(self, original_email, *args, **kwargs):
        super(AdminUpdateUserForm, self).__init__(*args, **kwargs)
        self.original_email = original_email

    def validate_email(self, email):
        if email.data != self.original_email:
            user = User.query.filter_by(email=email.data).first()
            if user:
                raise ValidationError('That email is already in use by another account.')

class AdminResetPasswordForm(FlaskForm):
    new_password = PasswordField('New Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Reset Password')

class RequestResetForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')

class NoteForm(FlaskForm):
    text = TextAreaField('Add a note', validators=[DataRequired()])
    submit = SubmitField('Post Note')

class ChangeStatusForm(FlaskForm):
    status = SelectField('New Status', choices=[
        'Open', 'Pending', 'Scheduled', 'Quote Sent', 'Completed', 'Cancelled'
    ], validators=[DataRequired()])
    scheduled_date = DateField('Scheduled Date', validators=[Optional()])
    submit = SubmitField('Update Status')

class AttachmentForm(FlaskForm):
    file = FileField('Upload Attachment', validators=[FileRequired()])
    submit = SubmitField('Upload')

class NewRequestForm(FlaskForm):
    wo_number = StringField('Work Order #', validators=[DataRequired()])
    request_type = SelectField('Type of Request', choices=request_types_list, validators=[DataRequired()])
    description = TextAreaField('Description / Instructions', validators=[DataRequired()])
    property = StringField('Property', validators=[DataRequired()])
    unit = StringField('Unit #', validators=[DataRequired()])
    address = StringField('Address')
    property_manager = StringField('Property Manager')
    tenant_name = StringField('Tenant Name')
    tenant_phone = StringField('Tenant Phone')
    contact_person = StringField('Contact Person', validators=[DataRequired()])
    contact_person_phone = StringField('Contact Person Phone', validators=[DataRequired()])
    attachments = MultipleFileField('Attachments', validators=[FileAllowed(['jpg', 'png', 'jpeg', 'gif', 'pdf', 'doc', 'docx'])])
    date_1 = DateField('Preferred Date 1', validators=[DataRequired()])
    date_2 = DateField('Preferred Date 2', validators=[DataRequired()])
    date_3 = DateField('Preferred Date 3', validators=[DataRequired()])
    submit = SubmitField('Submit Request')

class EditRequestForm(FlaskForm):
    wo_number = StringField('Work Order #', validators=[DataRequired()])
    request_type = SelectField('Type of Request', choices=request_types_list, validators=[DataRequired()])
    description = TextAreaField('Description / Instructions', validators=[DataRequired()])
    property = StringField('Property', validators=[DataRequired()])
    unit = StringField('Unit #', validators=[DataRequired()])
    date_1 = DateField('Preferred Date 1', validators=[DataRequired()])
    date_2 = DateField('Preferred Date 2', validators=[DataRequired()])
    date_3 = DateField('Preferred Date 3', validators=[DataRequired()])
    submit = SubmitField('Save Changes')

# --- Context Processor to inject notifications ---
@app.context_processor
def inject_notifications():
    if current_user.is_authenticated:
        unread_notifications = Notification.query.filter_by(user_id=current_user.id, is_read=False).order_by(Notification.timestamp.desc()).all()
        return dict(unread_notifications=unread_notifications)
    return dict(unread_notifications=[])

# --- Routes ---
@app.route('/')
@login_required
def index():
    if current_user.role in ['Requester', 'Property Manager']:
        return redirect(url_for('my_requests'))
    else:
        return redirect(url_for('dashboard'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role in ['Requester', 'Property Manager']:
        return redirect(url_for('my_requests'))

    query = WorkOrder.query
    
    # Search
    search_term = request.args.get('search')
    if search_term:
        query = query.filter(or_(
            WorkOrder.id.like(f'%{search_term}%'),
            WorkOrder.wo_number.like(f'%{search_term}%'),
            WorkOrder.property.like(f'%{search_term}%'),
            WorkOrder.address.like(f'%{search_term}%'),
            WorkOrder.requester_name.like(f'%{search_term}%')
        ))

    # Filters
    requester_filter = request.args.get('requester')
    property_filter = request.args.get('property')
    type_filter = request.args.get('type')

    if requester_filter:
        query = query.filter(WorkOrder.user_id == requester_filter)
    if property_filter:
        query = query.filter(WorkOrder.property == property_filter)
    if type_filter:
        query = query.filter(WorkOrder.request_type == type_filter)

    all_requests = query.order_by(WorkOrder.date_created.desc()).all()
    
    status_counts = Counter(req.status for req in WorkOrder.query.all())
    stats = {
        "totalRequests": len(WorkOrder.query.all()), "new": status_counts.get('New', 0),
        "open": status_counts.get('Open', 0), "pending": status_counts.get('Pending', 0),
        "scheduled": status_counts.get('Scheduled', 0), "cancelled": status_counts.get('Cancelled', 0),
        "completed": status_counts.get('Completed', 0),
    }
    status_data_for_chart = [{"name": status, "count": count} for status, count in status_counts.items()]
    type_counts = Counter(req.request_type for req in WorkOrder.query.all())
    type_data_for_chart = [{"name": type, "value": count} for type, count in type_counts.items()]

    # Data for filters
    all_requesters = User.query.filter_by(role='Requester').all()
    all_properties = Property.query.all()

    return render_template(
        'dashboard.html', title='Dashboard', stats=stats,
        status_data=json.dumps(status_data_for_chart),
        type_data=json.dumps(type_data_for_chart), requests=all_requests,
        all_requesters=all_requesters, all_properties=all_properties, request_types=request_types_list
    )

@app.route('/my-requests')
@login_required
def my_requests():
    query = WorkOrder.query
    if current_user.role == 'Property Manager':
        query = query.filter(WorkOrder.property_manager == current_user.name)
    else: # Requester
        query = query.filter_by(author=current_user)

    search_term = request.args.get('search')
    if search_term:
        query = query.filter(or_(
            WorkOrder.id.like(f'%{search_term}%'),
            WorkOrder.wo_number.like(f'%{search_term}%'),
            WorkOrder.property.like(f'%{search_term}%'),
            WorkOrder.request_type.like(f'%{search_term}%')
        ))

    user_requests = query.order_by(WorkOrder.date_created.desc()).all()
    return render_template('my_requests.html', title='My Requests', requests=user_requests)

@app.route('/requests/<status>')
@login_required
def requests_by_status(status):
    if current_user.role == 'Requester':
        abort(403)
    
    filtered_requests = WorkOrder.query.filter_by(status=status).order_by(WorkOrder.date_created.desc()).all()
    return render_template('requests_by_status.html', title=f'Requests: {status}', requests=filtered_requests, status=status)

@app.route('/request/<int:request_id>', methods=['GET', 'POST'])
@login_required
def view_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    if current_user.role == 'Requester' and work_order.author != current_user:
        abort(403)
    if current_user.role == 'Property Manager' and work_order.property_manager != current_user.name:
        abort(403)

    note_form = NoteForm()
    status_form = ChangeStatusForm()
    attachment_form = AttachmentForm()
    all_users = User.query.all()
    user_names = [user.name for user in all_users]

    if request.method == 'GET':
        if current_user.role in ['Admin', 'Scheduler', 'Super User'] and work_order.status == 'New':
            work_order.status = 'Open'
            db.session.add(AuditLog(text=f'Status changed to Open.', user_id=current_user.id, work_order_id=work_order.id))
            db.session.commit()
            flash('Request status has been updated to Open.', 'info')
        
        db.session.add(AuditLog(text=f'Viewed the request.', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()

    if note_form.validate_on_submit() and 'post_note' in request.form:
        note_text = note_form.text.data
        note = Note(text=note_text, author=current_user, work_order=work_order)
        db.session.add(note)
        
        notified_users = set()
        if work_order.author != current_user:
            notified_users.add(work_order.author)

        tagged_names = re.findall(r'@(\w+(?:\s\w+)?)', note_text)
        for name in tagged_names:
            tagged_user = User.query.filter(User.name.ilike(name.strip())).first()
            if tagged_user and tagged_user != current_user:
                notified_users.add(tagged_user)
        
        for user in notified_users:
            notification = Notification(
                text=f'{current_user.name} left a note on Request #{work_order.id}',
                link=url_for('view_request', request_id=work_order.id),
                user_id=user.id
            )
            db.session.add(notification)

        db.session.commit()
        flash('Your note has been added.', 'success')
        return redirect(url_for('view_request', request_id=work_order.id))

    notes = Note.query.filter_by(work_order_id=request_id).order_by(Note.date_posted.asc()).all()
    audit_logs = AuditLog.query.filter_by(work_order_id=request_id).order_by(AuditLog.timestamp.asc()).all()
    return render_template('view_request.html', title=f'Request #{work_order.id}', request=work_order, notes=notes, note_form=note_form, status_form=status_form, audit_logs=audit_logs, attachment_form=attachment_form, user_names=json.dumps(user_names))

@app.route('/change_status/<int:request_id>', methods=['POST'])
@login_required
def change_status(request_id):
    if current_user.role not in ['Admin', 'Scheduler', 'Super User']:
        abort(403)
    
    work_order = WorkOrder.query.get_or_404(request_id)
    form = ChangeStatusForm()
    if form.validate_on_submit():
        old_status = work_order.status
        new_status = form.status.data
        work_order.status = new_status
        
        if new_status == 'Scheduled' and form.scheduled_date.data:
            work_order.scheduled_date = form.scheduled_date.data
            log_text = f'Changed status from {old_status} to {new_status} for {work_order.scheduled_date.strftime("%Y-%m-%d")}.'
        else:
            work_order.scheduled_date = None
            log_text = f'Changed status from {old_status} to {new_status}.'
        
        db.session.add(AuditLog(text=log_text, user_id=current_user.id, work_order_id=work_order.id))

        notification = Notification(
            text=f'Status for Request #{work_order.id} changed to {new_status}.',
            link=url_for('view_request', request_id=work_order.id),
            user_id=work_order.user_id
        )
        db.session.add(notification)
        db.session.commit()
        flash(f'Status updated to {new_status}.', 'success')
    return redirect(url_for('view_request', request_id=request_id))

@app.route('/notifications/read/<int:notification_id>')
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.get_or_404(notification_id)
    if notification.user_id != current_user.id:
        abort(403)
    notification.is_read = True
    db.session.commit()
    return redirect(notification.link)

@app.route('/new-request', methods=['GET', 'POST'])
@login_required
def new_request():
    properties = Property.query.all()
    properties_dict = {p.name: {"address": p.address, "manager": p.property_manager} for p in properties}
    form = NewRequestForm()
    
    if form.validate_on_submit():
        new_order = WorkOrder(
            wo_number=form.wo_number.data, 
            requester_name=current_user.name,
            request_type=form.request_type.data, 
            description=form.description.data,
            property=form.property.data, 
            unit=form.unit.data,
            address=properties_dict.get(form.property.data, {}).get('address', ''),
            property_manager=properties_dict.get(form.property.data, {}).get('manager', ''),
            tenant_name=form.tenant_name.data,
            tenant_phone=form.tenant_phone.data,
            contact_person=form.contact_person.data,
            contact_person_phone=form.contact_person_phone.data,
            preferred_date_1=form.date_1.data,
            preferred_date_2=form.date_2.data,
            preferred_date_3=form.date_3.data,
            user_id=current_user.id
        )
        db.session.add(new_order)
        db.session.commit() # Commit to get the new_order.id

        for file in form.attachments.data:
            if file:
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                attachment = Attachment(filename=filename, user_id=current_user.id, work_order_id=new_order.id)
                db.session.add(attachment)

        # Notify Admins and Schedulers
        admins_and_schedulers = User.query.filter(User.role.in_(['Admin', 'Scheduler', 'Super User'])).all()
        for user in admins_and_schedulers:
            notification = Notification(
                text=f'New request #{new_order.id} submitted by {current_user.name}.',
                link=url_for('view_request', request_id=new_order.id),
                user_id=user.id
            )
            db.session.add(notification)

        db.session.commit()
        flash('Your request has been created!', 'success')
        return redirect(url_for('my_requests'))

    return render_template(
        'request_form.html', title='New Request', form=form,
        properties=properties, property_data=json.dumps(properties_dict)
    )

@app.route('/edit-request/<int:request_id>', methods=['GET', 'POST'])
@login_required
def edit_request(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    if work_order.author != current_user and current_user.role not in ['Admin', 'Scheduler', 'Super User']:
        abort(403)
    
    form = EditRequestForm()
    if form.validate_on_submit():
        work_order.wo_number = form.wo_number.data
        work_order.request_type = form.request_type.data
        work_order.description = form.description.data
        work_order.property = form.property.data
        work_order.unit = form.unit.data
        work_order.preferred_date_1 = form.date_1.data
        work_order.preferred_date_2 = form.date_2.data
        work_order.preferred_date_3 = form.date_3.data
        db.session.add(AuditLog(text='Edited request details.', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash('Request has been updated.', 'success')
        return redirect(url_for('view_request', request_id=work_order.id))

    form.wo_number.data = work_order.wo_number
    form.request_type.data = work_order.request_type
    form.description.data = work_order.description
    form.property.data = work_order.property
    form.unit.data = work_order.unit
    form.date_1.data = work_order.preferred_date_1
    form.date_2.data = work_order.preferred_date_2
    form.date_3.data = work_order.preferred_date_3
    return render_template('edit_request.html', title='Edit Request', form=form, request_id=request_id)

@app.route('/upload_attachment/<int:request_id>', methods=['POST'])
@login_required
def upload_attachment(request_id):
    work_order = WorkOrder.query.get_or_404(request_id)
    form = AttachmentForm()
    if form.validate_on_submit():
        file = form.file.data
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        attachment = Attachment(filename=filename, user_id=current_user.id, work_order_id=request_id)
        db.session.add(attachment)
        db.session.add(AuditLog(text=f'Uploaded attachment: {filename}', user_id=current_user.id, work_order_id=work_order.id))
        db.session.commit()
        flash('File uploaded successfully.', 'success')
    return redirect(url_for('view_request', request_id=request_id))

@app.route('/download_attachment/<int:attachment_id>')
@login_required
def download_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    return send_from_directory(app.config['UPLOAD_FOLDER'], attachment.filename, as_attachment=True)

@app.route('/delete_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    if attachment.user_id != current_user.id and current_user.role != 'Admin':
        abort(403)
    
    try:
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], attachment.filename))
    except OSError:
        pass

    db.session.add(AuditLog(text=f'Deleted attachment: {attachment.filename}', user_id=current_user.id, work_order_id=attachment.work_order_id))
    db.session.delete(attachment)
    db.session.commit()
    flash('Attachment deleted.', 'success')
    return redirect(url_for('view_request', request_id=attachment.work_order_id))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(name=form.name.data, email=form.email.data, role=form.role.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Your account has been created! You are now able to log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', title='Register', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Login Unsuccessful. Please check email and password.', 'danger')
    return render_template('login.html', title='Login', form=form)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_request():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            token = s.dumps(user.email, salt='password-reset-salt')
            msg = Message('Password Reset Request', sender='noreply@demo.com', recipients=[user.email])
            link = url_for('reset_token', token=token, _external=True)
            msg.html = render_template('reset_email.html', user=user, token=token)
            mail.send(msg)
        flash('If an account with that email exists, a password reset link has been sent.', 'info')
        return redirect(url_for('login'))
    return render_template('reset_request.html', title='Reset Password', form=form)

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        flash('The password reset link is expired.', 'warning')
        return redirect(url_for('reset_request'))
    except:
        flash('The password reset link is invalid.', 'warning')
        return redirect(url_for('reset_request'))
    
    user = User.query.filter_by(email=email).first()
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been updated! You are now able to log in.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_token.html', title='Reset Password', form=form)

@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    update_form = UpdateAccountForm()
    password_form = ChangePasswordForm()

    if 'update_account' in request.form and update_form.validate_on_submit():
        current_user.name = update_form.name.data
        current_user.email = update_form.email.data
        db.session.commit()
        flash('Your account has been updated!', 'success')
        return redirect(url_for('account'))

    if 'change_password' in request.form and password_form.validate_on_submit():
        if current_user.check_password(password_form.current_password.data):
            current_user.set_password(password_form.new_password.data)
            db.session.commit()
            flash('Your password has been changed!', 'success')
        else:
            flash('Incorrect current password.', 'danger')
        return redirect(url_for('account'))

    update_form.name.data = current_user.name
    update_form.email.data = current_user.email

    return render_template('account.html', title='Account', update_form=update_form, password_form=password_form)

@app.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_dashboard():
    upload_form = PropertyUploadForm()
    if upload_form.validate_on_submit():
        try:
            csv_file = upload_form.csv_file.data
            stream = io.StringIO(csv_file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.reader(stream)
            next(csv_reader, None) # Skip header
            Property.query.delete()
            for row in csv_reader:
                if len(row) == 3:
                    new_property = Property(name=row[0].strip(), address=row[1].strip(), property_manager=row[2].strip())
                    db.session.add(new_property)
            db.session.commit()
            flash('Properties have been successfully uploaded and updated.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred during upload: {e}', 'danger')
        return redirect(url_for('admin_dashboard'))

    all_users = User.query.all()
    return render_template('admin.html', title='Admin Dashboard', upload_form=upload_form, users=all_users)

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user_to_edit = User.query.get_or_404(user_id)
    
    # Security check for role hierarchy
    if current_user.role == 'Admin' and user_to_edit.role in ['Admin', 'Super User']:
        abort(403)

    update_form = AdminUpdateUserForm(original_email=user_to_edit.email)
    password_form = AdminResetPasswordForm()

    if 'update_user' in request.form and update_form.validate_on_submit():
        user_to_edit.name = update_form.name.data
        user_to_edit.email = update_form.email.data
        user_to_edit.role = update_form.role.data
        db.session.commit()
        flash(f'User {user_to_edit.name} has been updated.', 'success')
        return redirect(url_for('admin_dashboard'))
    
    if 'reset_password' in request.form and password_form.validate_on_submit():
        user_to_edit.set_password(password_form.new_password.data)
        db.session.commit()
        flash(f"Password for {user_to_edit.name} has been reset.", "success")
        return redirect(url_for('edit_user', user_id=user_id))

    update_form.name.data = user_to_edit.name
    update_form.email.data = user_to_edit.email
    update_form.role.data = user_to_edit.role
    
    return render_template('edit_user.html', title='Edit User', update_form=update_form, password_form=password_form, user=user_to_edit)

@app.route('/calendar')
@login_required
def calendar():
    return render_template('calendar.html', title='Calendar')

@app.route('/api/events')
@login_required
def api_events():
    query = WorkOrder.query.filter(WorkOrder.scheduled_date.isnot(None))
    if current_user.role == 'Requester':
        query = query.filter(WorkOrder.user_id == current_user.id)
    
    events = query.all()
    event_list = []
    for event in events:
        event_list.append({
            'title': f"Request #{event.id}",
            'start': event.scheduled_date.strftime('%Y-%m-%d'),
            'url': url_for('view_request', request_id=event.id)
        })
    return jsonify(event_list)

def setup_application(app_context):
    """Creates database and default admin user if they don't exist."""
    with app_context:
        db.create_all()
        if not User.query.filter_by(role='Super User').first():
            admin_email = 'superuser@example.com'
            admin_password = 'password'
            admin_user = User(name='Super User', email=admin_email, role='Super User')
            admin_user.set_password(admin_password)
            db.session.add(admin_user)
            db.session.commit()
            print('--- Default Super User Created ---')
            print(f'Email: {admin_email}')
            print(f'Password: {admin_password}')
            print('----------------------------------')

setup_application(app.app_context())

if __name__ == '__main__':
    app.run(debug=True)