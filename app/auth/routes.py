# app/auth/routes.py
from flask import render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, current_user
from itsdangerous import URLSafeTimedSerializer
from app import db
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
        if user and user.check_password(form.password.data) and user.is_active:
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('main.index'))
        else:
            flash('Login Unsuccessful. Please check email and password, or activate your account via the invitation link.', 'danger')
    return render_template('auth/login.html', title='Login', form=form)

@auth.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@auth.route('/set-password/<token>', methods=['GET', 'POST'])
def set_password(token):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = s.loads(token, salt='account-setup-salt', max_age=604800) # 7 days
    except:
        flash('The activation link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.login'))
    
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('Invalid user.', 'danger')
        return redirect(url_for('auth.login'))

    if user.is_active:
        flash('This account has already been activated. Please log in.', 'info')
        return redirect(url_for('auth.login'))

    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.is_active = True
        db.session.commit()
        flash('Your account has been activated! You are now able to log in.', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/set_password.html', title='Set Your Password', form=form, token=token)

@auth.route('/reset_password', methods=['GET', 'POST'])
def reset_request():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
            token = s.dumps(user.email, salt='password-reset-salt')
            email_body = f"""
            <p>To reset your password, please visit the following link. This link will expire in 1 hour.</p>
            """
            send_notification_email(
                subject="Password Reset Request",
                recipients=[user.email],
                text_body=f"To reset your password, please visit the following link: {url_for('auth.reset_token', token=token, _external=True)}",
                html_body=render_template(
                    'email/notification_email.html',
                    title="Password Reset",
                    user=user,
                    body_content=email_body,
                    link=url_for('auth.reset_token', token=token, _external=True)
                )
            )
        flash('If an account with that email exists, a password reset link has been sent.', 'info')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/reset_request.html', title='Reset Password', form=form)

@auth.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=3600) # 1 hour
    except:
        flash('The password reset link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.reset_request'))
    
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('Invalid user.', 'danger')
        return redirect(url_for('auth.login'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been updated! You are now able to log in.', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/reset_token.html', title='Reset Password', form=form, token=token)