from app import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

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
    password_hash = db.Column(db.String(128))
    role = db.Column(db.String(20), nullable=False, default='Requester')
    is_active = db.Column(db.Boolean, default=False, nullable=False)
    requests = db.relationship('WorkOrder', backref='author', lazy=True)
    notes = db.relationship('Note', backref='author', lazy=True)
    notifications = db.relationship('Notification', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if self.password_hash:
            return check_password_hash(self.password_hash, password)
        return False

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

class WorkOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wo_number = db.Column(db.String(100), nullable=False)
    requester_name = db.Column(db.String(100), nullable=False)
    request_type = db.Column(db.String(100), nullable=False)
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
    vendor_assigned = db.Column(db.String(100), nullable=True)
    date_created = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    scheduled_date = db.Column(db.Date, nullable=True)
    date_completed = db.Column(db.DateTime, nullable=True) # This line is essential
    preferred_date_1 = db.Column(db.Date, nullable=True)
    preferred_date_2 = db.Column(db.Date, nullable=True)
    preferred_date_3 = db.Column(db.Date, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    notes = db.relationship('Note', backref='work_order', lazy=True, cascade="all, delete-orphan")
    audit_logs = db.relationship('AuditLog', backref='work_order', lazy=True, cascade="all, delete-orphan")
    attachments = db.relationship('Attachment', backref='work_order', lazy=True, cascade="all, delete-orphan")
    viewers = db.relationship('User', secondary=work_order_viewers, lazy='subquery',
                              backref=db.backref('viewable_orders', lazy=True))

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
    file_type = db.Column(db.String(50), nullable=False, default='Attachment')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=False)
    user = db.relationship('User')

