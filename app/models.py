# app/models.py
from app.extensions import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from app.utils import get_denver_now, DENVER_TZ # <-- Import DENVER_TZ added here

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

work_order_viewers = db.Table('work_order_viewers',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('work_order_id', db.Integer, db.ForeignKey('work_order.id'), primary_key=True)
)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), nullable=False, default='Requester')
    is_active = db.Column(db.Boolean, default=False, nullable=False)
    last_message_read_time = db.Column(db.DateTime, nullable=True) # Set when user reads messages
    signature = db.Column(db.Text, nullable=True)

    requests = db.relationship('WorkOrder', backref='author', lazy='dynamic', cascade="all, delete-orphan")
    notes = db.relationship('Note', backref='author', lazy='dynamic', cascade="all, delete-orphan")
    notifications = db.relationship('Notification', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    messages_sent = db.relationship('Message',
                                    foreign_keys='Message.sender_id',
                                    backref='author', lazy='dynamic', cascade="all, delete-orphan")
    messages_received = db.relationship('Message',
                                        foreign_keys='Message.recipient_id',
                                        backref='recipient', lazy='dynamic', cascade="all, delete-orphan")
    attachments = db.relationship('Attachment', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    audit_logs = db.relationship('AuditLog', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    push_subscriptions = db.relationship('PushSubscription', backref='user', lazy='dynamic', cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if self.password_hash:
            return check_password_hash(self.password_hash, password)
        return False

    def new_messages(self):
        # Use Denver time for comparison baseline if last_message_read_time is None
        # Make a timezone-aware datetime far in the past
        baseline_past = get_denver_now().replace(year=1900, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        last_read_time = self.last_message_read_time or baseline_past
        # Ensure last_read_time is Denver-aware if loaded from DB potentially naive
        if last_read_time.tzinfo is None:
             last_read_time = DENVER_TZ.localize(last_read_time)

        # Assuming Message.timestamp is also Denver-aware now
        return Message.query.filter_by(recipient=self).filter(
            Message.timestamp > last_read_time).count()

    @staticmethod
    def create_default_superuser():
        if not User.query.filter_by(role='Super User').first():
            admin_email = 'superuser@example.com'
            admin_password = 'password'
            admin_user = User(name='Super User', email=admin_email, role='Super User', is_active=True)
            admin_user.set_password(admin_password)
            db.session.add(admin_user)
            db.session.commit()
            print('--- Default Super User Created ---')
            print(f'Email: {admin_email}')
            print(f'Password: {admin_password}')
            print('----------------------------------')

class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(150), unique=True, nullable=False)
    contact_name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    specialty = db.Column(db.String(255), nullable=True)
    website = db.Column(db.String(255), nullable=True)

    work_orders = db.relationship('WorkOrder', backref='vendor', lazy='dynamic')
    quotes = db.relationship('Quote', backref='vendor', lazy='dynamic')

    def __repr__(self):
        return f"Vendor('{self.company_name}')"

class WorkOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wo_number = db.Column(db.String(100), nullable=True)
    requester_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    property = db.Column(db.String(100), nullable=False)
    unit = db.Column(db.String(20), nullable=True)
    address = db.Column(db.String(200), nullable=False)
    property_manager = db.Column(db.String(100), nullable=True)
    tenant_name = db.Column(db.String(100), nullable=True)
    tenant_phone = db.Column(db.String(20), nullable=True)
    contact_person = db.Column(db.String(100), nullable=True)
    contact_person_phone = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='New')
    tag = db.Column(db.String(255), nullable=True)
    date_created = db.Column(db.DateTime, nullable=False, default=get_denver_now) # <-- Use Denver time default
    scheduled_date = db.Column(db.Date, nullable=True) # Date type has no timezone
    date_completed = db.Column(db.DateTime, nullable=True) # Set manually, ensure Denver time is used
    preferred_date_1 = db.Column(db.Date, nullable=True) # Date type
    preferred_date_2 = db.Column(db.Date, nullable=True) # Date type
    preferred_date_3 = db.Column(db.Date, nullable=True) # Date type
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id'), nullable=True)
    property_id = db.Column(db.Integer, db.ForeignKey('property.id'), nullable=True)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True) # Set manually, ensure Denver time is used
    approved_quote_id = db.Column(db.Integer, db.ForeignKey('quote.id'), nullable=True)
    follow_up_date = db.Column(db.Date, nullable=True) # Date type
    last_follow_up_sent = db.Column(db.DateTime, nullable=True) # Set manually, ensure Denver time is used
    preferred_vendor = db.Column(db.String(150), nullable=True)

    request_type_id = db.Column(db.Integer, db.ForeignKey('request_type.id'), nullable=False)

    notes = db.relationship('Note', backref='work_order', lazy=True, cascade="all, delete-orphan")
    audit_logs = db.relationship('AuditLog', backref='work_order', lazy=True, cascade="all, delete-orphan")
    attachments = db.relationship('Attachment', backref='work_order', lazy=True, cascade="all, delete-orphan")
    messages = db.relationship('Message', backref='work_order', lazy=True, cascade="all, delete-orphan")
    quotes = db.relationship('Quote', backref='work_order', lazy=True, cascade="all, delete-orphan", foreign_keys='Quote.work_order_id')

    viewers = db.relationship('User', secondary=work_order_viewers, lazy='subquery',
                              backref=db.backref('viewable_orders', lazy=True))

class Property(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    address = db.Column(db.String(200), nullable=False)
    property_manager = db.Column(db.String(100), nullable=True)
    work_orders = db.relationship('WorkOrder', backref='property_relation', lazy='dynamic')

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, nullable=False, default=get_denver_now) # <-- Use Denver time default
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=False)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=get_denver_now) # <-- Use Denver time default

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime, default=get_denver_now) # <-- Use Denver time default
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=False)

class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50), nullable=False, default='Attachment')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=False)
    quote = db.relationship('Quote', backref='attachment', uselist=False, cascade="all, delete-orphan")

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    sender_email = db.Column(db.String(120))
    recipient_email = db.Column(db.String(120))
    cc = db.Column(db.Text, nullable=True)
    subject = db.Column(db.String(255))
    body = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, index=True, default=get_denver_now) # <-- Use Denver time default
    is_read = db.Column(db.Boolean, default=False)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=True)
    attachments = db.relationship('MessageAttachment', backref='message', lazy=True, cascade="all, delete-orphan")

class MessageAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)

class Quote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date_sent = db.Column(db.DateTime, nullable=False, default=get_denver_now) # <-- Use Denver time default
    # *** MODIFICATION: Make status nullable ***
    status = db.Column(db.String(50), nullable=True, default='Pending')
    # *** END MODIFICATION ***
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=False)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id'), nullable=False)
    attachment_id = db.Column(db.Integer, db.ForeignKey('attachment.id'), nullable=False, unique=True)

class RequestType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    work_orders = db.relationship('WorkOrder', backref='request_type_relation', lazy='dynamic')

    def __repr__(self):
        return f"RequestType('{self.name}')"

class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subscription_json = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)