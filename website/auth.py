from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from .models import User, CoinHistory
from werkzeug.security import generate_password_hash, check_password_hash
from . import db, mail, oauth
from flask_login import login_user, login_required, logout_user, current_user
from flask_mail import Message
import secrets
import datetime
import re 
import requests
import os
import random
import string
from threading import Thread

auth = Blueprint('auth', __name__)

# ==========================================
# ✅ HELPER 1: Download & Save Image
# ==========================================
def download_image(image_url, name_prefix):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(image_url, headers=headers, timeout=10)
        if response.status_code == 200:
            upload_folder = os.path.join(current_app.root_path, 'static', 'uploads')
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)
            
            clean_name = re.sub(r'[^a-zA-Z0-9]', '', str(name_prefix))
            filename = f"avatar_{clean_name}_{secrets.token_hex(4)}.png"
            file_path = os.path.join(upload_folder, filename)
            
            with open(file_path, 'wb') as f:
                f.write(response.content)
            return filename
    except Exception as e:
        print(f"Image Download Error: {e}")
    return None

# ==========================================
# ✅ HELPER 2: CLEAN DEVICE NAME
# ==========================================
def get_clean_device_info(user_agent):
    if not user_agent:
        return "Unknown Device"
    
    os_name = "Unknown Device"
    if "Windows" in user_agent: os_name = "Windows PC"
    elif "Macintosh" in user_agent: os_name = "Mac"
    elif "iPhone" in user_agent: os_name = "iPhone"
    elif "Android" in user_agent: os_name = "Android Mobile"
    elif "Linux" in user_agent: os_name = "Linux System"
    
    browser = "Browser"
    if "Edg" in user_agent: browser = "Edge"
    elif "Chrome" in user_agent and "Edg" not in user_agent: browser = "Chrome"
    elif "Safari" in user_agent and "Chrome" not in user_agent: browser = "Safari"
    elif "Firefox" in user_agent: browser = "Firefox"
    
    return f"{os_name} • {browser}"

# ==========================================
# ✅ HELPER 3: SEND PROFESSIONAL EMAILS
# ==========================================
def send_async_email(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
            print(f"✅ Email sent to {msg.recipients}")
        except Exception as e:
            print(f"❌ Email Failed: {e}")

def send_professional_email(user, type="login"):
    app = current_app._get_current_object()
    brand_color = "#166534"
    website_name = "GyanBarter"
    
    if type == "welcome":
        subject = f"Welcome to {website_name}! 🚀"
        title = f"Welcome, {user.first_name}!"
        body_content = f"""
            <p>We are thrilled to have you on board. <b>GyanBarter</b> is the best place to exchange skills and grow together.</p>
            <p style="font-size: 18px;">🎁 <b>You earned 50 Free Coins!</b></p>
            <p>Start exploring skills or list your own to earn more.</p>
        """
        btn_text = "Go to Dashboard"
        btn_link = url_for('views.dashboard', _external=True)

    elif type == "login":
        subject = f"Security Alert: New Login on {website_name}"
        title = "New Login Detected 🛡️"
        raw_agent = request.headers.get('User-Agent', '')
        clean_device = get_clean_device_info(raw_agent)
        current_time = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
        
        body_content = f"""
            <p>We noticed a new login to your <b>{website_name}</b> account.</p>
            <div style="background-color: #f0fdf4; padding: 15px; border-radius: 5px; border-left: 5px solid {brand_color}; margin: 15px 0;">
                <p style="margin: 5px 0;"><b>🕒 Time:</b> {current_time}</p>
                <p style="margin: 5px 0;"><b>💻 Device:</b> {clean_device}</p>
                <p style="margin: 5px 0;"><b>📧 Email:</b> {user.email}</p>
            </div>
            <p>If this was you, you can ignore this email. If not, please secure your account.</p>
        """
        btn_text = "Secure My Account"
        btn_link = url_for('auth.forgot_password', _external=True)

    html_template = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
        <div style="background-color: {brand_color}; padding: 25px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0; font-size: 24px;">{website_name}</h1>
        </div>
        <div style="padding: 30px;">
            <h2 style="color: #333333; margin-top: 0;">{title}</h2>
            <div style="color: #555555; font-size: 16px; line-height: 1.6;">
                {body_content}
            </div>
            <div style="text-align: center; margin-top: 30px; margin-bottom: 20px;">
                <a href="{btn_link}" style="background-color: {brand_color}; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 50px; font-weight: bold; display: inline-block;">{btn_text}</a>
            </div>
        </div>
        <div style="background-color: #f4f4f4; padding: 15px; text-align: center; color: #888888; font-size: 12px;">
            <p>&copy; {datetime.datetime.now().year} {website_name}. All rights reserved.</p>
            <p>Automated message. Please do not reply.</p>
        </div>
    </div>
    """

    msg = Message(subject, recipients=[user.email], html=html_template)
    Thread(target=send_async_email, args=(app, msg)).start()


# ==========================================
# 1. LOGIN ROUTE 🔒
# ==========================================
@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()
        if user:
            db.session.refresh(user) 
            
            if user.account_status != 'Active':
                flash(f'Login Denied. Your account is {user.account_status}.', category='error')
                return redirect(url_for('auth.login'))

            if check_password_hash(user.password, password):
                if user.email == "gyanbarter@gmail.com":
                    print(f"🚩 SUPER ADMIN LOGGED IN: {user.email}")

                if user.profile_pic == 'default.png':
                    user.profile_pic = user.generate_avatar_url()
                    db.session.commit()
                
                login_user(user, remember=True)
                send_professional_email(user, type="login")
                
                flash('Logged in successfully!', category='success')
                return redirect(url_for('views.dashboard'))
            else:
                flash('Incorrect password.', category='error')
        else:
            flash('Email does not exist.', category='error')

    return render_template("login.html", user=current_user)

# ==========================================
# 2. GOOGLE LOGIN (Fixed Username 🛡️)
# ==========================================
@auth.route('/login/google')
def google_login():
    try:
        redirect_uri = url_for('auth.google_callback', _external=True)
        return oauth.google.authorize_redirect(redirect_uri, prompt='select_account')
    except Exception as e:
        flash('Error connecting to Google.', category='error')
        return redirect(url_for('auth.login'))

@auth.route('/login/google/callback')
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        resp = oauth.google.get('https://www.googleapis.com/oauth2/v3/userinfo')
        user_info = resp.json()
        
        user_email = user_info.get('email').strip().lower()
        user_name = user_info.get('name', 'User')
        google_pic_url = user_info.get('picture') 
        
        if not user_email:
            flash('Google did not return an email.', category='error')
            return redirect(url_for('auth.login'))
        
        user = User.query.filter_by(email=user_email).first()
        local_image_name = download_image(google_pic_url, user_email.split('@')[0]) if google_pic_url else None

        if not user:
            # ✅ USERNAME GENERATION LOGIC
            generated_username = user_email.split('@')[0]
            # डुप्लीकेट चेक
            if User.query.filter_by(username=generated_username).first():
                generated_username += str(random.randint(10, 99))

            new_user = User(
                email=user_email,
                username=generated_username, # ✅ REQUIRED FIX
                first_name=user_name.split()[0],
                password=generate_password_hash(secrets.token_hex(16)), 
                is_profile_complete=False,
                is_admin=False,
                account_status='Active',
                coins=50 
            )
            
            new_user.profile_pic = local_image_name or 'default.png'
            db.session.add(new_user)
            db.session.commit()

            db.session.add(CoinHistory(user_id=new_user.id, amount=50, transaction_type="Signup Bonus (Google)"))
            db.session.commit()

            user = new_user
            send_professional_email(user, type="welcome")
            flash('Account created via Google!', category='success')
        else:
            db.session.refresh(user)
            if user.account_status != 'Active':
                flash('Google login failed. Account Restricted.', category='error')
                return redirect(url_for('auth.login'))

            if local_image_name and (user.profile_pic == 'default.png' or 'http' in user.profile_pic):
                user.profile_pic = local_image_name
                db.session.commit()
            
            send_professional_email(user, type="login")
        
        login_user(user, remember=True)
        return redirect(url_for('views.dashboard'))
    except Exception as e:
        print(f"GOOGLE ERROR: {str(e)}")
        flash('Google Login Failed.', category='error')
        return redirect(url_for('auth.login'))

# ✅ सहायता के लिए: यूनिक यूजरनेम बनाने वाला फंक्शन
def generate_unique_username(prefix):
    while True:
        suffix = ''.join(random.choices(string.digits, k=4))
        new_username = f"{prefix}_{suffix}"
        # पक्का करें कि यह डेटाबेस में पहले से न हो
        if not User.query.filter_by(username=new_username).first():
            return new_username

# ==========================================
# 3. FACEBOOK LOGIN (Fully Automated 🛡️)
# ==========================================

@auth.route('/facebook-login')
def facebook_login():
    """यूज़र को फेसबुक के आधिकारिक लॉगिन पेज पर भेजता है"""
    try:
        # डैशबोर्ड में सेट किया गया Redirect URI पक्का करें
        redirect_uri = url_for('auth.facebook_authorize', _external=True)
        return oauth.facebook.authorize_redirect(redirect_uri)
    except Exception as e:
        print(f"Facebook Redirect Error: {e}")
        flash('Could not connect to Facebook. Try again later.', category='error')
        return redirect(url_for('auth.login'))

@auth.route('/facebook-authorize')
def facebook_authorize():
    """फेसबुक से डेटा लेकर यूज़र को लॉगिन या रजिस्टर करता है"""
    try:
        token = oauth.facebook.authorize_access_token()
        # हम फेसबुक से id, नाम और ईमेल मांग रहे हैं
        resp = oauth.facebook.get('me?fields=id,first_name,last_name,email')
        user_info = resp.json()
        
        user_email = user_info.get('email', '').strip().lower()
        first_name = user_info.get('first_name', 'User')
        last_name = user_info.get('last_name', '')
        
        if not user_email:
            flash('Facebook email not found. Please try Google or Email signup.', category='error')
            return redirect(url_for('auth.login'))

        # 1. चेक करें कि क्या यूज़र पहले से डेटाबेस में है
        user = User.query.filter_by(email=user_email).first()

        if not user:
            # 2. ✅ नया यूज़र रजिस्टर करें (ऑटो-यूज़रनेम के साथ)
            email_prefix = user_email.split('@')[0]
            generated_username = generate_unique_username(email_prefix)

            new_user = User(
                email=user_email,
                username=generated_username, 
                first_name=first_name,
                last_name=last_name,
                # एक बहुत मजबूत रैंडम पासवर्ड (सुरक्षा के लिए)
                password=generate_password_hash(secrets.token_hex(16), method='pbkdf2:sha256'),
                is_admin=False,
                account_status='Active',
                coins=50 # फेसबुक साइन-अप बोनस
            )
            
            db.session.add(new_user)
            db.session.commit()
            
            # बोनस कॉइन की एंट्री लेज़र में डालें
            db.session.add(CoinHistory(
                user_id=new_user.id, 
                amount=50, 
                transaction_type="Signup Bonus (Facebook)"
            ))
            db.session.commit()
            
            user = new_user
            # स्वागत ईमेल भेजें
            # from .utils import send_professional_email 
            # send_professional_email(user, type="welcome")
            flash(f'Welcome! Your unique ID is @{generated_username}', category='success')
        
        else:
            # 3. अगर यूज़र पहले से है, तो उसका स्टेटस चेक करें
            if user.account_status != 'Active':
                flash(f'Your account is {user.account_status}. Please contact support.', category='error')
                return redirect(url_for('auth.login'))
            
            # लॉगिन ईमेल भेजें (वैकल्पिक)
            # send_professional_email(user, type="login")
            flash('Successfully logged in with Facebook!', category='success')

        # 4. यूज़र को लॉगिन करवाएं
        login_user(user, remember=True)
        return redirect(url_for('views.dashboard'))

    except Exception as e:
        print(f"Facebook Auth Error: {e}")
        flash('Facebook Authentication failed.', category='error')
        return redirect(url_for('auth.login'))

# ==========================================
# 4. SIGNUP ROUTE (White Theme & Optional)
# ========================================== 

@auth.route('/sign-up', methods=['GET', 'POST'])
def sign_up():
    if request.method == 'POST':
        # 1. डेटा कलेक्ट करें
        email = request.form.get('email', '').strip().lower()
        first_name = request.form.get('firstName', '').strip()
        last_name = request.form.get('lastName', '').strip()
        password = request.form.get('password')
        country = request.form.get('country')  # ✅ ऑप्शनल
        phone = request.form.get('phone', '').strip()  # ✅ ऑप्शनल

        # पासवर्ड मजबूती के लिए Regex पैटर्न
        password_pattern = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
        
        # 2. ✅ स्मार्ट और यूनिक यूज़रनेम जनरेशन
        email_prefix = email.split('@')[0] if email else "user"
        
        def generate_unique_username(prefix):
            """डेटाबेस में चेक करके यूनिक यूज़रनेम बनाता है"""
            while True:
                suffix = ''.join(random.choices(string.digits, k=4))
                new_username = f"{prefix}_{suffix}"
                # क्या यह यूज़रनेम पहले से है?
                if not User.query.filter_by(username=new_username).first():
                    return new_username

        username = generate_unique_username(email_prefix)

        # 3. वैलिडेशन चेक
        user_email_exists = User.query.filter_by(email=email).first()

        if user_email_exists:
            flash('Email already exists. Please login.', category='error')
        elif not email or not password:
            flash('Email and Password are required fields.', category='error')
        elif not re.match(password_pattern, password):
            flash('Password too weak! Use Upper, Lower, Number, and Symbol (Min 8 chars).', category='error')
        elif len(first_name) < 2:
            flash('First name must be at least 2 characters.', category='error')
        else:
            # 4. ✅ नया यूज़र ऑब्जेक्ट (ऑप्शनल डेटा के साथ)
            new_user = User(
                email=email, 
                first_name=first_name, 
                last_name=last_name,
                username=username, # सिस्टम द्वारा जेनरेटेड
                password=generate_password_hash(password, method='pbkdf2:sha256'),
                country=country if country else None, # अगर नहीं है तो None
                phone=phone if phone else None,       # अगर नहीं है तो None
                coins=50, # वेलकम बोनस
                is_admin=False,
                account_status='Active'
            )
            
            # 5. अवतार (Profile Pic) सेटअप
            try:
                # यूज़रनेम के आधार पर अवतार जेनरेट और डाउनलोड करना
                avatar_url = new_user.generate_avatar_url()
                saved_filename = download_image(avatar_url, first_name)
                new_user.profile_pic = saved_filename if saved_filename else 'default.png'
            except Exception as img_err:
                print(f"⚠️ Avatar generation skipped: {img_err}")
                new_user.profile_pic = 'default.png'

            # 6. डेटाबेस में सुरक्षित करें
            try:
                db.session.add(new_user)
                db.session.commit()
                
                # कॉइन हिस्ट्री में साइन-अप बोनस एंट्री
                bonus = CoinHistory(
                    user_id=new_user.id, 
                    amount=50, 
                    transaction_type="Signup Bonus"
                )
                db.session.add(bonus)
                db.session.commit()
                
                # स्वागत ईमेल (अगर इनेबल है)
                # send_professional_email(new_user, type="welcome")
                
                flash(f'Welcome aboard! Your auto-generated username is @{username}. You can change it later in profile.', category='success')
                return redirect(url_for('auth.login'))

            except Exception as e:
                db.session.rollback()
                print(f"❌ Database Error: {e}")
                flash('Something went wrong. Please try again later.', category='error')

    return render_template("sign_up.html", user=current_user)

# ✅ ईमेल को बैकग्राउंड में भेजने के लिए हेल्पर फंक्शन
def send_async_email(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
        except Exception as e:
            print(f"Email Error: {e}")

# ==========================================
# 6. FORGOT & RESET PASSWORD (Final Logic)
# ==========================================

@auth.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        
        user = User.query.filter_by(email=email).first()
        if user:
            # 1. अकाउंट स्टेटस चेक करें
            if user.account_status != 'Active':
                flash('Account is restricted. Contact support.', category='error')
                return redirect(url_for('auth.login'))

            # 2. टोकन जनरेट करें (1 घंटे के लिए वैलिड)
            token = secrets.token_hex(20)
            user.reset_token = token
            user.token_expiration = datetime.datetime.now() + datetime.timedelta(hours=1)
            db.session.commit()
            
            # 3. रिसेट लिंक बनाएँ
            reset_url = url_for('auth.reset_password', token=token, _external=True)
            
            # 4. प्रोफेशनल ईमेल बॉडी
            html_body = f"""
            <div style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
                <h2 style="color: #3a1b5d;">Reset Your Password</h2>
                <p>Hello {user.first_name},</p>
                <p>We received a request to reset the password for your GyanBarter account.</p>
                <p>Click the button below to set a new password:</p>
                <a href="{reset_url}" style="background-color: #f39c12; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">Reset Password</a>
                <p style="margin-top: 20px; font-size: 12px; color: #777;">This link expires in 1 hour. If you didn't request this, please ignore this email.</p>
                <hr>
                <p style="font-size: 12px; color: #999;">GyanBarter Team | Mumbai, India</p>
            </div>
            """
            
            # 5. ईमेल भेजें (Async Thread)
            msg = Message('Password Reset Request - GyanBarter', recipients=[user.email])
            msg.html = html_body
            # Sender Default आपके config से लेगा
            
            Thread(target=send_async_email, args=(current_app._get_current_object(), msg)).start()
            
            flash('Password reset link has been sent to your email.', category='success')
            return redirect(url_for('auth.login'))
        
        else:
            # सुरक्षा के लिए हम यही कहेंगे कि अगर ईमेल है तो लिंक भेज दिया गया है
            # लेकिन अभी टेस्टिंग के लिए आप Error दिखा सकते हैं
            flash('No account found with that email address.', category='error')

    return render_template("forgot_password.html", user=current_user)


@auth.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    # 1. टोकन वेरीफाई करें
    user = User.query.filter_by(reset_token=token).first()
    
    # चेक करें कि यूजर मिला या नहीं और टोकन एक्सपायर तो नहीं हुआ
    if not user or not user.token_expiration or user.token_expiration < datetime.datetime.now():
        flash('Invalid or expired reset link. Please try again.', category='error')
        return redirect(url_for('auth.forgot_password'))
        
    if request.method == 'POST':
        new_password = request.form.get('password')
        
        if len(new_password) < 8:
            flash('Password must be at least 8 characters long.', category='error')
        else:
            # 2. नया पासवर्ड सेट करें
            user.password = generate_password_hash(new_password, method='pbkdf2:sha256')
            
            # 3. टोकन साफ़ करें (ताकि दोबारा यूज़ न हो सके)
            user.reset_token = None 
            user.token_expiration = None
            db.session.commit()
            
            flash('Password changed successfully! You can now login.', category='success')
            return redirect(url_for('auth.login'))
            
    # यहाँ आपको एक reset_password.html बनाना पड़ेगा (नीचे दिया है)
    return render_template("reset_password.html", token=token, user=current_user)


# ✅ लॉगआउट करने का मास्टर रूट
@auth.route('/logout')
@login_required # पक्का करें कि केवल लॉगिन यूज़र ही लॉगआउट कर सके
def logout():
    logout_user() # यूज़र का सेशन डिलीट करें
    flash('You have been logged out. See you soon! 👋', category='success')
    return redirect(url_for('auth.login')) # वापस लॉगिन पेज पर भेजें