# app/auth/routes.py
from flask import render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, current_user
from itsdangerous import URLSafeTimedSerializer, SignatureExpired
from flask_mail import Message

from app import db, mail
from app.auth import auth
from app.models import User
from app.forms import LoginForm, RequestResetForm, ResetPasswordForm, SetPasswordForm

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            if not user.is_active:
                flash('Your account has not been activated yet. Please check your email for the activation link.', 'warning')
                return redirect(url_for('auth.login'))
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.index'))
        else:
            flash('Login Unsuccessful. Please check email and password.', 'danger')
    return render_template('login.html', title='Login', form=form)

@auth.route('/set-password/<token>', methods=['GET', 'POST'])
def set_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = s.loads(token, salt='account-setup-salt', max_age=86400) # 24 hours
    except (SignatureExpired, Exception):
        flash('The invitation link is invalid or has expired.', 'warning')
        return redirect(url_for('auth.login'))
    
    user = User.query.filter_by(email=email).first()
    if user is None or user.is_active:
        flash('Invalid invitation link.', 'warning')
        return redirect(url_for('auth.login'))
        
    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.is_active = True
        db.session.commit()
        flash('Your account has been activated! You are now able to log in.', 'success')
        return redirect(url_for('auth.login'))
        
    return render_template('set_password.html', title='Set Your Password', form=form)

@auth.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

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
            msg = Message('Password Reset Request',
                          sender=current_app.config['MAIL_DEFAULT_SENDER'],
                          recipients=[user.email])
            msg.html = render_template('reset_email.html', user=user, token=token)
            mail.send(msg)
        flash('If an account with that email exists, a password reset link has been sent.', 'info')
        return redirect(url_for('auth.login'))
    return render_template('reset_request.html', title='Reset Password', form=form)

@auth.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=3600)
    except (SignatureExpired, Exception):
        flash('The password reset link is invalid or has expired.', 'warning')
        return redirect(url_for('auth.reset_request'))
    
    user = User.query.filter_by(email=email).first()
    if user is None:
        flash('Invalid user.', 'warning')
        return redirect(url_for('auth.reset_request'))
        
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been updated! You are now able to log in.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('reset_token.html', title='Reset Password', form=form)