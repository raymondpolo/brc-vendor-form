# app/forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, SelectField, TextAreaField, MultipleFileField, HiddenField, FloatField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError, Optional, URL
from flask_wtf.file import FileAllowed, FileField
from flask_login import current_user
# MODIFIED: Import RequestType
from app.models import User, Vendor, RequestType
from wtforms.widgets import HiddenInput
from wtforms_sqlalchemy.fields import QuerySelectField
from datetime import datetime

# Custom validator to handle empty strings for optional unique fields
class OptionalUnique(Optional):
    def __call__(self, form, field):
        if not field.data:
            field.data = None
        super().__call__(form, field)

def date_format(form, field):
    """Custom validator to ensure date string is in MM/DD/YYYY format."""
    if field.data:
        try:
            # Check if it's already a date object (might happen if populated from obj)
            if isinstance(field.data, datetime.date):
                return # Already valid
            datetime.strptime(field.data, '%m/%d/%Y')
        except (ValueError, TypeError):
            raise ValidationError('Date must be in MM/DD/YYYY format.')

class MessageForm(FlaskForm):
    recipient = StringField('To', validators=[DataRequired(), Email()])
    cc = StringField('CC (optional, comma-separated)', validators=[Optional()])
    subject = StringField('Subject', validators=[DataRequired()])
    body = TextAreaField('Message', validators=[DataRequired()])
    attachments = MultipleFileField('Attachments')
    work_order_id = StringField('Work Order ID', widget=HiddenInput(), validators=[Optional()])
    sender_choice = SelectField('Send From', choices=[], validators=[DataRequired()])
    submit = SubmitField('Send Message')

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
        ('custom_range', 'Custom Date Range')
    ], validators=[DataRequired()])
    start_date = StringField('Start Date', validators=[Optional(), date_format])
    end_date = StringField('End Date', validators=[Optional(), date_format])

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
    csv_file = FileField('Properties CSV File', validators=[DataRequired(), FileAllowed(['csv'])])
    submit = SubmitField('Upload')

class VendorUploadForm(FlaskForm):
    csv_file = FileField('Vendors CSV File', validators=[DataRequired(), FileAllowed(['csv'])])
    submit = SubmitField('Upload')

class UpdateAccountForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    # The signature field is removed from the form, it will be handled directly in the template
    submit = SubmitField('Update Account')

    def __init__(self, *args, **kwargs):
        super(UpdateAccountForm, self).__init__(*args, **kwargs)
        # Store original email only if current_user exists (during app context)
        if current_user and hasattr(current_user, 'email'):
            self.original_email = current_user.email
        else:
            self.original_email = None # Handle cases outside request context if needed

    def validate_email(self, email):
        if email.data != self.original_email:
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
        'Open',
        'Pending',
        'Quote Requested',
        'Quote Sent',
        'Scheduled',
        'Completed',
        'Closed',
        'Cancelled'
    ], validators=[DataRequired()])
    scheduled_date = StringField('Scheduled Date', validators=[Optional(), date_format])
    # +++ ADD Follow-up fields +++
    add_follow_up = BooleanField('Add Follow-up Tag')
    follow_up_date = StringField('Follow-up Date', validators=[Optional(), date_format])
    # +++ END ADD +++
    submit = SubmitField('Update Status')

    # Custom validation for follow-up date
    def validate_follow_up_date(self, field):
        if self.add_follow_up.data and not field.data:
            raise ValidationError('Follow-up date is required when adding the follow-up tag.')
        # Date format validation is handled by date_format validator


def get_vendors():
    return Vendor.query.order_by(Vendor.company_name)

class AssignVendorForm(FlaskForm):
    vendor_id = HiddenField('Vendor ID', validators=[DataRequired()])
    submit = SubmitField('Assign')

class AttachmentForm(FlaskForm):
    file = MultipleFileField('Upload Attachment', validators=[DataRequired(), FileAllowed(['jpg', 'png', 'jpeg', 'gif', 'pdf', 'doc', 'docx'])]) # Added FileAllowed
    submit = SubmitField('Upload')

class NewRequestForm(FlaskForm):
    wo_number = StringField('Work Order #', validators=[Optional()])
    request_type = SelectField('Type of Request', coerce=int, validators=[DataRequired()])
    description = TextAreaField('Description / Instructions', validators=[DataRequired()])
    property = StringField('Property', validators=[DataRequired()])
    unit = StringField('Unit #', validators=[Optional()])
    address = StringField('Address') # Auto-filled, maybe make Optional validator?
    property_manager = StringField('Property Manager') # Auto-filled
    tenant_name = StringField('Tenant Name', validators=[Optional()])
    tenant_phone = StringField('Tenant Phone', validators=[Optional()])
    contact_person = StringField('Contact Person', validators=[Optional()])
    contact_person_phone = StringField('Contact Person Phone', validators=[Optional()])
    vendor_assigned = StringField('Preferred Vendor (Optional)')
    attachments = MultipleFileField('Attachments', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif', 'pdf', 'doc', 'docx'])])
    date_1 = StringField('Preferred Date 1', validators=[DataRequired(), date_format])
    date_2 = StringField('Preferred Date 2', validators=[DataRequired(), date_format])
    date_3 = StringField('Preferred Date 3', validators=[DataRequired(), date_format])
    submit = SubmitField('Submit Request')

class PropertyForm(FlaskForm):
    name = StringField('Property Name', validators=[DataRequired()])
    address = StringField('Address', validators=[DataRequired()])
    property_manager = SelectField('Property Manager', choices=[], validators=[Optional()])
    submit = SubmitField('Save Property')

class VendorForm(FlaskForm):
    company_name = StringField('Vendor', validators=[DataRequired()])
    contact_name = StringField('Contact', validators=[Optional()])
    email = StringField('Email Address', validators=[OptionalUnique(), Email()])
    phone = StringField('Phone Number', validators=[Optional()])
    specialty = StringField('Specialty (e.g., Plumbing)', validators=[DataRequired()])
    website = StringField('Website', validators=[OptionalUnique(), URL()])
    submit = SubmitField('Save Vendor')

class QuoteForm(FlaskForm):
    vendor = QuerySelectField('Vendor', query_factory=get_vendors, get_label='company_name', allow_blank=False, validators=[DataRequired()])
    quote_file = FileField('Quote File', validators=[DataRequired(), FileAllowed(['pdf', 'doc', 'docx', 'jpg', 'png', 'jpeg'])])
    submit = SubmitField('Upload Quote')

class DeleteRestoreRequestForm(FlaskForm):
    """An empty form for CSRF protection."""
    pass

# --- REMOVED GoBackForm ---
class GoBackForm(FlaskForm):
    """Backwards-compatible form for toggling the Go-back tag.

    Some parts of the code import `GoBackForm` directly; this minimal form
    provides CSRF protection when toggling the Go-back tag via POST.
    """
    pass

class TagForm(FlaskForm):
    # --- REMOVED Follow-up and Go-back ---
    tag = SelectField('Tag', choices=[
        #('Awaiting Approval', 'Awaiting Approval'), # Handled via status/quotes
        #('Follow-up needed', 'Follow-up needed'), # Moved to ChangeStatusForm
        #('Completed', 'Completed'), # Added automatically
        #('Go-back', 'Go-back') # Handled via checkbox
        # Add any OTHER tags here if needed
    ], validators=[Optional()]) # Make optional if no choices left
    # --- REMOVED follow_up_date ---
    # follow_up_date = StringField('Follow-up Date', validators=[Optional(), date_format])
    submit = SubmitField('Add Tag')

# ADDED: Form for adding/editing request types
class RequestTypeForm(FlaskForm):
    name = StringField('Request Type Name', validators=[DataRequired()])
    submit = SubmitField('Save')

def get_requesters():
    # Fetch active requesters
    return User.query.filter_by(role='Requester', is_active=True).order_by(User.name)

class ReassignRequestForm(FlaskForm):
    requester = QuerySelectField('New Requester', query_factory=get_requesters, get_label='name', allow_blank=False, validators=[DataRequired()])
    submit = SubmitField('Reassign')

class MarkAsCompletedForm(FlaskForm):
    """An empty form for the 'Mark as Completed' button."""
    submit = SubmitField('Mark as Completed')

class SendFollowUpForm(FlaskForm):
    """Form for sending a manual follow-up email."""
    recipient = StringField('To', validators=[DataRequired(), Email()])
    cc = StringField('CC (optional, comma-separated)', validators=[Optional()]) # Removed Email validator for flexibility
    subject = StringField('Subject', validators=[DataRequired()])
    body = TextAreaField('Body', validators=[DataRequired()])
    submit = SubmitField('Send Follow-Up')

# Form specifically for the Go-Back toggle via JS/AJAX
class ToggleTagForm(FlaskForm):
    """CSRF protection for simple tag toggles."""
    pass