from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import URLSafeTimedSerializer, SignatureExpired
from app import db, mail
from app.auth import auth
from app.models import User
from app.forms import LoginForm, RequestResetForm, ResetPasswordForm, SetPasswordForm
from app.email import send_notification_email

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            if user.is_active:
                login_user(user, remember=form.remember.data)
                next_page = request.args.get('next')
                return redirect(next_page) if next_page else redirect(url_for('main.index'))
            else:
                flash('Your account has not been activated yet. Please check your email for the activation link.', 'warning')
        else:
            flash('Login Unsuccessful. Please check email and password', 'danger')
    # Corrected line below
    return render_template('auth/login.html', title='Login', form=form)

@auth.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.index'))

@auth.route('/set-password/<token>', methods=['GET', 'POST'])
def set_password(token):
    s = URLSafeTimedSerializer(db.get_app().config['SECRET_KEY'])
    try:
        email = s.loads(token, salt='account-setup-salt', max_age=86400) # 24 hours
    except SignatureExpired:
        flash('The activation link has expired.', 'danger')
        return redirect(url_for('auth.login'))
    
    user = User.query.filter_by(email=email).first_or_404()
    if user.is_active:
        flash('Account already activated. Please log in.', 'info')
        return redirect(url_for('auth.login'))

    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.is_active = True
        db.session.commit()
        flash('Your account has been activated! You can now log in.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/set_password.html', title='Set Your Password', form=form)

@auth.route('/reset_password', methods=['GET', 'POST'])
def reset_request():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            s = URLSafeTimedSerializer(db.get_app().config['SECRET_KEY'])
            token = s.dumps(user.email, salt='password-reset-salt')
            email_body = f"""
            <p>To reset your password, visit the following link:</p>
            <p>This link is valid for 30 minutes.</p>
            """
            send_notification_email(
                subject="Password Reset Request",
                recipients=[user.email],
                html_body=render_template(
                    'email/notification_email.html',
                    title="Password Reset Request",
                    user=user,
                    body_content=email_body,
                    link=url_for('auth.reset_token', token=token, _external=True)
                )
            )
        flash('An email has been sent with instructions to reset your password.', 'info')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_request.html', title='Reset Password', form=form)

@auth.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    s = URLSafeTimedSerializer(db.get_app().config['SECRET_KEY'])
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=1800) # 30 minutes
    except SignatureExpired:
        flash('The password reset link is invalid or has expired.', 'warning')
        return redirect(url_for('auth.reset_request'))
    
    user = User.query.filter_by(email=email).first()
    if user is None:
        flash('Invalid or expired token.', 'warning')
        return redirect(url_for('auth.reset_request'))
        
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been updated! You are now able to log in.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_token.html', title='Reset Password', form=form)
