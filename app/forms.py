from flask_wtf import FlaskForm
from wtforms import (StringField, PasswordField, SubmitField, BooleanField, 
                     SelectField, TextAreaField, DateField, MultipleFileField,
                     RadioField)
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError, Optional
from wtforms.widgets import HiddenInput
from flask_wtf.file import FileField, FileAllowed, FileRequired
from flask_login import current_user
from app.models import User

class ReportForm(FlaskForm):
    date_type = SelectField('Filter by Date Type', choices=[
        ('date_created', 'Creation Date'),
        ('date_completed', 'Completion Date')
    ], validators=[DataRequired()])
    date_range = SelectField('Date Range', choices=[
        ('all', 'All Time'),
        ('today', 'Today'),
        ('yesterday', 'Yesterday'),
        ('this_week', 'This Week'),
        ('last_week', 'Last Week'),
        ('this_month', 'This Month'),
        ('last_month', 'Last Month'),
        ('this_year', 'This Year'),
        ('last_year', 'Last Year'),
        ('custom_date', 'Custom Date'),
        ('custom_range', 'Custom Range')
    ], default='all', validators=[DataRequired()])
    start_date = DateField('Start Date', validators=[Optional()])
    end_date = DateField('End Date', validators=[Optional()])

class MessageForm(FlaskForm):
    recipient = StringField('To', validators=[DataRequired(), Email()])
    sender_choice = SelectField('Send From', validators=[DataRequired()])
    subject = StringField('Subject', validators=[DataRequired()])
    body = TextAreaField('Message', validators=[DataRequired()])
    work_order_id = StringField('Work Order ID', widget=HiddenInput(), validators=[Optional()])
    submit = SubmitField('Send Message')

class InviteUserForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    role = SelectField('Role', choices=['Requester', 'Scheduler', 'Property Manager', 'Admin'], validators=[DataRequired()])
    submit = SubmitField('Send Invitation')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('That email is already in use. Please choose a different one.')

class AddUserForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    role = SelectField('Role', choices=['Requester', 'Scheduler', 'Property Manager', 'Admin'], validators=[DataRequired()])
    submit = SubmitField('Add User')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('That email is already in use.')

class SetPasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Set Password and Activate Account')

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
        'Open', 'Pending', 'Quote Sent', 'Scheduled', 'Closed', 'Cancelled'
    ], validators=[DataRequired()])
    scheduled_date = DateField('Scheduled Date', validators=[Optional()])
    submit = SubmitField('Update Status')

class AssignVendorForm(FlaskForm):
    vendor_assigned = StringField('Vendor Name', validators=[DataRequired()])
    submit = SubmitField('Assign')

class AttachmentForm(FlaskForm):
    file = FileField('Upload Attachment', validators=[FileRequired()])
    submit = SubmitField('Upload')

class NewRequestForm(FlaskForm):
    wo_number = StringField('Work Order #', validators=[DataRequired()])
    request_type = SelectField('Type of Request', choices=[
        'Appliance', 'Junk Removal', 'Plumbing', 'Pest Control', 'Electrical',
        'Painting', 'Cleaning', 'Fence', 'Power Wash', 'Flooring', 'Window'
    ], validators=[DataRequired()])
    description = TextAreaField('Description / Instructions', validators=[DataRequired()])
    property = StringField('Property', validators=[DataRequired()])
    unit = StringField('Unit #', validators=[Optional()])
    address = StringField('Address')
    property_manager = StringField('Property Manager')
    tenant_name = StringField('Tenant Name')
    tenant_phone = StringField('Tenant Phone')
    contact_person = StringField('Contact Person', validators=[Optional()])
    contact_person_phone = StringField('Contact Person Phone', validators=[Optional()])
    vendor_assigned = StringField('Preferred Vendor (Optional)', validators=[Optional()])
    attachments = MultipleFileField('Attachments', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif', 'pdf', 'doc', 'docx'])])
    date_1 = DateField('Preferred Date 1', validators=[DataRequired()])
    date_2 = DateField('Preferred Date 2', validators=[DataRequired()])
    date_3 = DateField('Preferred Date 3', validators=[DataRequired()])
    submit = SubmitField('Submit Request')
    
class PropertyForm(FlaskForm):
    name = StringField('Property Name', validators=[DataRequired()])
    address = StringField('Address', validators=[DataRequired()])
    property_manager = SelectField('Property Manager', validators=[Optional()])
    submit = SubmitField('Save Property')

