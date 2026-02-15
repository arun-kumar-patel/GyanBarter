# ==========================================
# 📂 STANDARD IMPORTS
# ==========================================
import os
import math
import random
import datetime
from datetime import timedelta
from io import BytesIO
from threading import Thread

# ==========================================
# 🌐 FLASK IMPORTS
# ==========================================
from flask import (
    Blueprint, render_template, request, flash, redirect, 
    url_for, current_app, jsonify, make_response
)
from flask_login import login_required, current_user, logout_user
from werkzeug.utils import secure_filename
from flask_mail import Message as EmailMessage
# ✅ Change your top import line to this:
from flask import render_template, flash, redirect, url_for, abort

# ==========================================
# 🗄️ DATABASE & MODELS
# ==========================================
from . import db, socketio, mail
from .models import (
    Skill, TradeRequest, User, Message,  # 💬 यह चैट वाला मैसेज है
    CoinHistory, Rating, Notification
)

# ==========================================
# 📧 EMAIL & SOCKET IMPORTS
# ==========================================
from flask_socketio import emit, join_room, leave_room

# 🔥 CRITICAL FIX: ईमेल मैसेज को अलग नाम दिया
from flask_mail import Message as EmailMessage 

# ==========================================
# 📄 PDF GENERATION IMPORTS
# ==========================================
from playwright.sync_api import sync_playwright
# from xhtml2pdf import pisa  <-- (इसकी अब ज़रूरत नहीं है क्योंकि हम Playwright यूज़ कर रहे हैं, हटा सकते हैं)

views = Blueprint('views', __name__)

# =========================================================
# 🛡️ 0. GLOBAL SECURITY GUARD (Instant Protection ✨)
# =========================================================
@views.before_app_request
def check_account_security():
    """यह फंक्शन हर क्लिक पर चेक करेगा कि एडमिन ने यूजर को ब्लॉक तो नहीं किया।"""
    if current_user.is_authenticated:
        # ✅ REFRESH: डेटाबेस से ताज़ा स्टेटस उठाएं ताकि 'Active' होते ही ताला खुल जाए
        db.session.refresh(current_user)
        
        # अगर अकाउंट Active नहीं है (Suspended या Banned है)
        if current_user.account_status != 'Active':
            # उसे सिर्फ लॉगआउट और स्टैटिक फाइल्स की इज़ाज़त दें
            allowed_endpoints = ['auth.logout', 'static', 'auth.login']
            if request.endpoint and request.endpoint not in allowed_endpoints:
                reason = current_user.status_reason or "Violation of community guidelines."
                flash(f"🔒 Access Denied: Your account is {current_user.account_status}. Reason: {reason}", "error")
                logout_user() # उसे तुरंत लॉगआउट कर दें
                return redirect(url_for('auth.login'))

# =========================================================
# 1. MAINTENANCE & HELPER FUNCTIONS
# =========================================================

def auto_release_escrow():
    """Safety: अगर छात्र 48 घंटे तक 'Complete' नहीं दबाता, तो पेमेंट ऑटो-रिलीज कर दो।"""
    try:
        limit = datetime.datetime.now() - datetime.timedelta(hours=48)
        pending_payments = TradeRequest.query.join(Skill).filter(
            TradeRequest.status == 'Accepted',
            TradeRequest.is_released == False,
            Skill.session_start < limit
        ).all()

        for req in pending_payments:
            teacher = User.query.get(req.receiver_id)
            if teacher and req.escrow_balance > 0:
                teacher.coins += req.escrow_balance
                db.session.add(CoinHistory(user_id=teacher.id, amount=req.escrow_balance, transaction_type=f"Auto-Released: {req.skill.title}"))
                req.is_released = True
                req.status = 'Completed'
                req.escrow_balance = 0
                db.session.add(Notification(user_id=teacher.id, message=f"Payment for '{req.skill.title}' auto-released to your wallet."))
        
        db.session.commit()
    except Exception as e:
        print(f"Maintenance Error (Escrow): {e}")

def check_expired_requests():
    """24 घंटे पुरानी पेंडिंग रिक्वेस्ट को हटाकर रिफंड।"""
    try:
        limit = datetime.datetime.now() - datetime.timedelta(hours=24)
        expired = TradeRequest.query.filter(TradeRequest.status == 'Pending', TradeRequest.timestamp < limit).all()
        for req in expired:
            req.status = 'Expired'
            student = User.query.get(req.sender_id)
            if student:
                student.coins += req.escrow_balance 
                db.session.add(CoinHistory(user_id=student.id, amount=req.escrow_balance, transaction_type="Refund: Request Timeout"))
                req.escrow_balance = 0
        db.session.commit()
    except:
        db.session.rollback()

def check_spin_expiry(user):
    """Lucky Spin कॉइन्स की 7 दिन की वैलिडिटी चेक करें।"""
    try:
        now = datetime.datetime.now()
        if user.spin_coins > 0 and user.spin_coins_expiry and now > user.spin_coins_expiry:
            expired_amount = user.spin_coins
            user.coins = max(0, user.coins - expired_amount) 
            user.spin_coins = 0           
            db.session.add(CoinHistory(user_id=user.id, amount=-expired_amount, transaction_type="Spin Coins Expired"))
            db.session.commit()
    except: pass

# =========================================================
# 2. CORE SYSTEM (DASHBOARD & COINS)
# =========================================================

@views.route('/')
def home():
    return render_template("home.html", user=current_user)

# ✅ सिक्कों का लेनदेन इतिहास (Transaction History)
@views.route('/coin-history')
@login_required
def coin_history():
    """यूजर के सिक्कों का पूरा हिसाब-किताब दिखाने के लिए मास्टर रूट"""
    # 1. डेटाबेस से वर्तमान यूजर की पूरी हिस्ट्री निकालें (Latest First)
    history = CoinHistory.query.filter_by(
        user_id=current_user.id
    ).order_by(CoinHistory.timestamp.desc()).all()
    
    # 2. 'history' डेटा को टेम्पलेट (HTML) में भेजें
    return render_template("coin_history.html", user=current_user, history=history)

@views.route('/download-statement', methods=['GET', 'POST'])
@login_required
def download_statement():
    if request.method == 'POST':
        quick_filter = request.form.get('quick_filter')
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')
        now = datetime.datetime.now()

        if quick_filter:
            start_date = now - datetime.timedelta(days=int(quick_filter))
            end_date = now
        elif start_date_str and end_date_str:
            try:
                start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d')
                end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59)
            except ValueError:
                flash("Invalid date format.", category='error')
                return redirect(url_for('views.coin_history'))
        else:
            return redirect(url_for('views.coin_history'))

        # ट्रांजेक्शन फिल्टर करें
        transactions = CoinHistory.query.filter(
            CoinHistory.user_id == current_user.id,
            CoinHistory.timestamp >= start_date,
            CoinHistory.timestamp <= end_date
        ).order_by(CoinHistory.timestamp.desc()).all()

        total_credit = sum(t.amount for t in transactions if t.amount > 0)
        total_debit = sum(abs(t.amount) for t in transactions if t.amount < 0)

        return render_template("statement_print.html", 
                               user=current_user, transactions=transactions, 
                               start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'),
                               total_credit=total_credit, total_debit=total_debit,
                               net_balance=total_credit - total_debit, now=now)
                               
    return redirect(url_for('views.coin_history'))
# =========================================================
# 3. GAMIFICATION (Daily Check-in, Spin, Leaderboard)
# =========================================================

@views.route('/daily-checkin', methods=['POST'])
@login_required
def daily_checkin():
    today = datetime.date.today()
    if current_user.last_checkin_date == today:
        return jsonify({"status": "error", "message": "Already claimed today!"}), 400

    if current_user.last_checkin_date == today - datetime.timedelta(days=1):
        current_user.checkin_streak += 1
    else:
        current_user.checkin_streak = 1 

    if current_user.checkin_streak > 7:
        current_user.checkin_streak = 1

    streak = current_user.checkin_streak
    reward = 5 if streak == 7 else (2 if streak >= 4 else 1)

    current_user.coins += reward
    current_user.last_checkin_date = today
    db.session.add(CoinHistory(user_id=current_user.id, amount=reward, transaction_type=f"Daily Check-in: Day {streak}"))
    db.session.commit()

    return jsonify({"status": "success", "reward": reward, "streak": streak})

@views.route('/leaderboard')
@login_required
def leaderboard():
    # 1. सबसे ज्यादा सिक्के कमाने वाले (Top Earners)
    top_earners = User.query.order_by(User.coins.desc()).limit(10).all()
    
    # 2. सबसे अच्छी रेटिंग वाले (Top Rated - जिनके पास कम से कम 1 रिव्यू हो)
    all_users = User.query.all()
    top_rated = sorted(
        [u for u in all_users if u.get_review_count() > 0], 
        key=lambda x: x.get_avg_rating(), 
        reverse=True
    )[:10]
    
    # हम टेम्पलेट में top_rated को 'teachers' के नाम से भेज रहे हैं ताकि मेडल लॉजिक काम करे
    return render_template(
        "leaderboard.html", 
        user=current_user, 
        top_earners=top_earners, 
        teachers=top_rated
    )

@views.route('/spin-wheel', methods=['GET', 'POST'])
@login_required
def spin_wheel():
    now = datetime.datetime.now()
    if request.method == 'POST':
        if current_user.last_spin_date and (now - current_user.last_spin_date).days < 7:
            return jsonify({"error": "Already spun this week!"}), 400
        won = random.randint(1, 10)
        current_user.coins += won
        current_user.spin_coins = won
        current_user.spin_coins_expiry = now + datetime.timedelta(days=7)
        current_user.last_spin_date = now
        db.session.add(CoinHistory(user_id=current_user.id, amount=won, transaction_type="Lucky Spin Win"))
        db.session.commit()
        return jsonify({"amount": won, "expiry": current_user.spin_coins_expiry.strftime('%d %b %Y')})
    return render_template("spin.html", user=current_user)

# =========================================================
# 5. MARKETPLACE & TRADES
# =========================================================

@views.route('/market')
@login_required
def market():
    # सर्च बॉक्स और कैटेगरी फिल्टर से डेटा लेना
    q = request.args.get('q')
    cat = request.args.get('category')
    
    # सिर्फ वही स्किल्स दिखाएं जो अभी एक्सपायर नहीं हुई हैं
    query = Skill.query.filter(Skill.expiry_date > datetime.datetime.now())
    
    if q:
        # टाइटल या डिस्क्रिप्शन में शब्द ढूंढना (ilike का उपयोग सर्च को आसान बनाता है)
        query = query.filter(
            (Skill.title.ilike(f'%{q}%')) | 
            (Skill.description.ilike(f'%{q}%'))
        )
        
    if cat and cat != 'All':
        query = query.filter_by(skill_type=cat)
    
    # नई पोस्ट को सबसे ऊपर दिखाएं
    skills = query.order_by(Skill.date_posted.desc()).all()
    
    return render_template("market.html", user=current_user, skills=skills, now=datetime.datetime.now())

# ==========================================
# ➕ ADD NEW SKILL (Classroom Creation)
# ==========================================
@views.route('/add-skill', methods=['GET', 'POST'])
@login_required
def add_skill():
    """नई स्किल पोस्ट करने और क्लासरूम बनाने के लिए मास्टर रूट"""
    if request.method == 'POST':
        # 1. फॉर्म से बेसिक डेटा प्राप्त करें
        title = request.form.get('title')
        desc = request.form.get('description')
        cost = request.form.get('cost')
        
        # ✅ FIX 1: फॉर्म के 'skill_type' को मॉडल के 'category' में डालें
        category_val = request.form.get('skill_type', 'General') 
        
        # ✅ FIX 2: डिफिकल्टी लेवल कैप्चर करें
        diff_level = request.form.get('difficulty_level', 'Beginner (Level 1)')

        # ✅ 3. Duration Logic: वैल्यू और यूनिट को मिनटों में बदलें
        duration_val = request.form.get('duration_value')
        duration_unit = request.form.get('duration_unit')
        
        try:
            val = int(duration_val) if duration_val else 1
            if duration_unit == "Minutes":
                total_minutes = val
                duration_display = f"{val} Minutes"
            elif duration_unit == "Hours":
                total_minutes = val * 60
                duration_display = f"{val} Hours"
            elif duration_unit == "Days":
                total_minutes = val * 1440
                duration_display = f"{val} Days"
            else:
                total_minutes = 60
                duration_display = "1 Hour"
        except:
            total_minutes = 60
            duration_display = "1 Hour"

        # ✅ 4. Post Expiry Logic (Permanent vs Hours)
        expiry_choice = request.form.get('expiry_limit')
        if expiry_choice == "permanent":
            # भविष्य की दूर की तारीख (2099) ताकि लिस्टिंग डिलीट न हो
            expiry_date = datetime.datetime(2099, 1, 1)
        else:
            try:
                hours = int(expiry_choice) if expiry_choice else 4
                expiry_date = datetime.datetime.now() + datetime.timedelta(hours=hours)
            except ValueError:
                expiry_date = datetime.datetime.now() + datetime.timedelta(hours=4)

        try:
            # 5. Session Start Time parsing
            session_start_str = request.form.get('session_start')
            if session_start_str:
                session_start_dt = datetime.datetime.strptime(session_start_str, '%Y-%m-%dT%H:%M')
            else:
                session_start_dt = datetime.datetime.now()

            # ✅ 6. Final Skill Creation (Database entry)
            new_skill = Skill(
                title=title,
                description=desc,
                cost=int(cost) if cost else 10,
                category=category_val,  # Mapping form skill_type to db category
                difficulty_level=diff_level, 
                duration=duration_display,
                duration_minutes=total_minutes,
                session_start=session_start_dt,
                expiry_date=expiry_date,
                user_id=current_user.id
            )
            
            db.session.add(new_skill)
            db.session.commit()
            
            flash(f'🚀 Classroom "{title}" created successfully!', 'success')
            # 'BuildError' से बचने के लिए पक्का करें कि views.dashboard मौजूद है
            return redirect(url_for('views.dashboard'))
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Database Creation Error: {e}")
            flash(f"Error creating class: {str(e)}", "error")
        
    return render_template("add_skill.html", user=current_user)

# ==========================================
# 🗑️ 1. SINGLE DELETE SKILL (Admin + Owner Power)
# ==========================================
@views.route('/delete-skill/<int:id>', methods=['POST'])
@login_required
def delete_skill(id):
    """
    किसी एक स्किल को डिलीट करने के लिए।
    """
    skill = Skill.query.get_or_404(id)
    
    # ✅ सुरक्षा चेक: केवल मालिक या एडमिन ही डिलीट कर सकता है
    if skill.user_id == current_user.id or current_user.is_admin:
        try:
            db.session.delete(skill)
            db.session.commit()
            flash(f'✅ Skill "{skill.title}" has been removed.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'❌ Error deleting skill: {str(e)}', 'error')
    else:
        flash('❌ Access Denied: You do not have permission to delete this.', 'error')
        
    return redirect(url_for('views.dashboard'))


# ==========================================
# 🗑️ BULK DELETE SKILLS (Final Master Fix)
# ==========================================
@views.route('/bulk-delete-skills', methods=['POST'])
@login_required
def bulk_delete_skills():
    """
    चेकबॉक्स के जरिए चुनी गई सभी स्किल्स को एक साथ डिलीट करने के लिए।
    """
    # 1. फॉर्म से 'skill_ids' की लिस्ट प्राप्त करें
    skill_ids = request.form.getlist('skill_ids')
    
    if not skill_ids:
        flash("⚠️ Please select at least one skill to remove.", "warning")
        return redirect(url_for('views.dashboard'))

    count = 0
    try:
        for s_id in skill_ids:
            # ID को सुरक्षित रूप से Integer में बदलें
            skill = db.session.get(Skill, int(s_id))
            
            # ✅ सुरक्षा: क्या यह स्किल आपकी है या आप एडमिन हैं?
            if skill and (skill.user_id == current_user.id or current_user.is_admin):
                db.session.delete(skill)
                count += 1
        
        # सभी डिलीट ऑपरेशन्स को एक साथ सबमिट करें
        db.session.commit()
        
        if count > 0:
            flash(f"🎉 Success! {count} skill listings removed from your dashboard.", "success")
        else:
            flash("❌ Verification failed: You can only delete your own skills.", "error")
            
    except Exception as e:
        db.session.rollback()
        print(f"🔥 Bulk Delete Error: {e}")
        flash("❌ System Error: Could not complete deletion.", "error")
        
    return redirect(url_for('views.dashboard'))

# =========================================================
# ✅ 6. TRADE, ESCROW & SAFETY
# =========================================================

# --- ❗ ज़रूरी इम्पोर्ट्स (Naming Conflict से बचने के लिए) ---
from flask_mail import Message as MailMessage 
import datetime
from .models import Message # डेटाबेस चैट मॉडल

@views.route('/request-trade/<int:skill_id>')
@login_required
def request_trade(skill_id):
    skill = Skill.query.get_or_404(skill_id)
    
    # 1. सुरक्षा चेक: खुद की स्किल जॉइन नहीं कर सकते
    if skill.user_id == current_user.id:
        flash("You cannot join your own class!", category='error')
        return redirect(url_for('views.market'))

    # ✅ 2. डुप्लीकेट चेक: पक्का करें कि रिक्वेस्ट पहले से मौजूद तो नहीं है
    existing_req = TradeRequest.query.filter_by(
        sender_id=current_user.id, 
        skill_id=skill.id
    ).first()

    if existing_req:
        if existing_req.status == 'Pending':
            flash("Wait! You already have a pending request for this class.", "warning")
        else:
            flash(f"You already have an active/completed trade for this: {existing_req.status}", "info")
        return redirect(url_for('views.dashboard'))
    
    # 3. बैलेंस चेक
    if current_user.coins < skill.cost:
        flash(f"Insufficient balance! You need {skill.cost} coins.", category='error')
        return redirect(url_for('views.market'))

    try:
        # 4. असली कटौती (छात्र के बैलेंस को तुरंत कम करेगा)
        current_user.coins -= skill.cost
        
        # 5. रिक्वेस्ट बनाना (Status: Pending)
        new_req = TradeRequest(
            sender_id=current_user.id, 
            receiver_id=skill.user_id, 
            skill_id=skill.id,
            escrow_balance=skill.cost, # पैसे 'तिजोरी' में लॉक हुए
            message=f"Joined class: {skill.title}",
            status='Pending'
        )
        
        # 6. हिस्ट्री में रिकॉर्ड जोड़ना
        history = CoinHistory(
            user_id=current_user.id, 
            amount=-skill.cost, 
            transaction_type=f"Escrow Hold: {skill.title}"
        )
        
        db.session.add(new_req)
        db.session.add(history)
        
        # 7. डेटाबेस में सेव करना
        db.session.commit() 
        flash(f"Success! {skill.cost} coins are held in Escrow.", category='success')
        
    except Exception as e:
        db.session.rollback()
        flash("Transaction failed. Please try again.", category='error')
        print(f"Trade Error: {e}")

    return redirect(url_for('views.dashboard'))


# --- 📧 Async Email Helper ---
def send_async_email(app, msg):
    with app.app_context():
        from . import mail
        mail.send(msg)

# --- 1️⃣ Single Request Management (Accept/Reject) ---
@views.route('/manage-request/<int:req_id>/<string:action>')
@login_required
def manage_request(req_id, action):
    req = TradeRequest.query.get_or_404(req_id)
    student = User.query.get(req.sender_id) # Student is the sender
    
    if req.receiver_id != current_user.id:
        flash("Unauthorized access!", category='error')
        return redirect(url_for('views.dashboard'))

    if action == 'accept' and req.status == 'Pending':
        req.status = 'Accepted'
        # ✅ Acceptance actions (Notification + Email)
        add_acceptance_actions(req, student)
        flash(f"Request for {student.first_name} has been accepted!", category='success')

    elif action == 'reject' and req.status == 'Pending':
        req.status = 'Rejected'
        # 💰 Refund Logic: Return escrow to student
        if req.escrow_balance > 0:
            student.coins += req.escrow_balance
            db.session.add(CoinHistory(user_id=student.id, amount=req.escrow_balance, transaction_type=f"Refund: {req.skill.title}"))
            req.escrow_balance = 0
            
        db.session.add(Notification(user_id=student.id, message=f"Your request for '{req.skill.title}' was declined. Coins refunded."))
        flash("Request Rejected. Coins have been refunded to the student.", category='info')

    db.session.commit()
    return redirect(url_for('views.dashboard'))

# ✅ ईमेल भेजने के लिए हेल्पर फंक्शन (Async)
def send_async_email(app, msg):
    with app.app_context():
        try:
            from . import mail # mail ऑब्जेक्ट को यहाँ इंपोर्ट करें
            mail.send(msg)
        except Exception as e:
            print(f"📧 Mail Error: {e}")

# --- 🛠️ Utility: Acceptance Actions (Notification + Email) ---
def add_acceptance_actions(req, student):
    """
    यह फंक्शन स्टूडेंट के लिए बेल नोटिफिकेशन बनाएगा और ईमेल भेजेगा।
    """
    # 1. Internal Dashboard Notification (बेल आइकन के लिए)
    notif_msg = f"Success! Your request for '{req.skill.title}' has been accepted. Check your dashboard."
    new_notif = Notification(
        user_id=student.id, 
        message=notif_msg,
        is_read=False
    )
    db.session.add(new_notif)

    # 2. Background Email Notification
    try:
        app = current_app._get_current_object()
        # स्टूडेंट को डैशबोर्ड पर भेजने के लिए लिंक
        join_url = url_for('views.dashboard', _external=True) 
        
        msg = MailMessage(
            subject="✅ GyanBarter: Your Skill Swap Request is Accepted!",
            recipients=[student.email]
        )
        
        # 'email_accepted.html' टेम्पलेट का उपयोग (जिसमें category फिक्स है)
        msg.html = render_template(
            "email_accepted.html", 
            student=student, 
            skill=req.skill, 
            join_url=join_url
        )
        
        # ईमेल को अलग थ्रेड में भेजें ताकि पेज लोड होने में देरी न हो
        Thread(target=send_async_email, args=(app, msg)).start()
        
    except Exception as e:
        print(f"⚠️ Email Error in Acceptance: {e}")

# --- 2️⃣ Bulk Accept Route (Updated & Final) ---
@views.route('/bulk-accept-requests', methods=['POST'])
@login_required
def bulk_accept_requests():
    # फॉर्म से सिलेक्ट किए गए सभी चेकबॉक्स की IDs उठाएं
    request_ids = request.form.getlist('request_ids') 
    
    if not request_ids:
        flash("Please select at least one request!", "warning")
        return redirect(url_for('views.dashboard'))

    count = 0
    for req_id in request_ids:
        # डेटाबेस से रिक्वेस्ट निकालें
        req = TradeRequest.query.get(req_id)
        
        # सुरक्षा चेक: क्या यह रिक्वेस्ट इसी टीचर की है और क्या यह अभी पेंडिंग है?
        if req and req.receiver_id == current_user.id and req.status == 'Pending':
            req.status = 'Accepted'
            
            # स्टूडेंट का डेटा निकालें
            student = User.query.get(req.sender_id)
            if student:
                # बेल नोटिफिकेशन और ईमेल भेजें
                add_acceptance_actions(req, student)
                count += 1
    
    # सभी बदलावों को एक साथ सेव करें
    db.session.commit()
    
    if count > 0:
        flash(f"🎉 Success! {count} requests accepted. Students notified via Dashboard & Email.", "success")
    else:
        flash("No valid pending requests found.", "info")
        
    return redirect(url_for('views.dashboard'))

# =========================================================
# ✅ 7. CLASSROOM, ATTENDANCE & AUTOMATIC PAYMENTS (Elite)
# =========================================================
from flask_mail import Message as MailMessage 
from .models import Message, Skill, TradeRequest, User, CoinHistory, Notification
import datetime 
from datetime import timedelta # रिफंड कैलकुलेशन के लिए ज़रूरी
import random
from io import BytesIO
from xhtml2pdf import pisa
from flask import jsonify, make_response, render_template, redirect, url_for, flash, request

# --- 🛠️ HELPER: Auto-Payment & No-Show Refund Logic ---
def run_maintenance():
    """सिस्टम चेक: ऑटो-पेमेंट (टीचर के लिए) और नो-शो रिफंड (छात्र के लिए)"""
    now = datetime.datetime.now()
    
    # --- 1. 💰 AUTO-PAY TEACHER (अगर छात्र ने जॉइन किया पर बटन नहीं दबाया) ---
    # नियम: जॉइन करने के 24 घंटे बाद टीचर को पैसे मिल जाएँ
    pay_threshold = now - timedelta(hours=24)
    overdue_pay = TradeRequest.query.filter(
        TradeRequest.status == 'Accepted',
        TradeRequest.session_start_time <= pay_threshold,
        TradeRequest.session_start_time.isnot(None), # छात्र ने जॉइन किया था ✅
        TradeRequest.is_released == False
    ).all()

    for req in overdue_pay:
        teacher = User.query.get(req.receiver_id)
        if teacher:
            teacher.coins += req.escrow_balance
            req.status = 'Completed'
            req.is_released = True
            db.session.add(CoinHistory(user_id=teacher.id, amount=req.escrow_balance, transaction_type=f"Auto-Attendance (24h): {req.skill.title}"))
            req.escrow_balance = 0

    # --- 2. ⚡ NO-SHOW REFUND (छात्र ने जॉइन नहीं किया तो क्लास खत्म होने के 1-2h बाद) ⚡ ---
    # नियम: अगर क्लास खत्म हुए 1.5 घंटे बीत गए और छात्र ने 'Join' नहीं किया, तो रिफंड
    pending_refunds = TradeRequest.query.filter(
        TradeRequest.status == 'Accepted',
        TradeRequest.session_start_time.is_(None), # कभी जॉइन नहीं किया ❌
        TradeRequest.is_released == False
    ).all()

    for req in pending_refunds:
        skill = req.skill
        if skill.session_start:
            # क्लास खत्म होने का समय + 1.5 घंटे का बफर (Buffer)
            # हम मान रहे हैं कि क्लास 1 घंटे की है, इसलिए स्टार्ट टाइम से 2.5 घंटे बाद रिफंड ट्रिगर होगा
            refund_trigger_time = skill.session_start + timedelta(hours=2, minutes=30)

            if now >= refund_trigger_time:
                student = User.query.get(req.sender_id)
                if student:
                    student.coins += req.escrow_balance
                    req.status = 'Expired' # रिक्वेस्ट बंद
                    req.is_released = True
                    db.session.add(CoinHistory(user_id=student.id, amount=req.escrow_balance, transaction_type=f"No-Show Refund: {req.skill.title}"))
                    req.escrow_balance = 0
                    db.session.add(Notification(user_id=student.id, message=f"Refunded: You missed '{req.skill.title}' and class has ended."))

    db.session.commit()

# पक्का करें कि ये ऊपर इम्पोर्टेड हैं:
# from datetime import datetime
# from .models import User, Skill, TradeRequest, Message

@views.route('/classroom/<int:skill_id>')
@login_required
def classroom(skill_id):
    skill = Skill.query.get_or_404(skill_id)
    is_teacher = (current_user.id == skill.user_id)
    
    # 1. ✅ स्मार्ट रिक्वेस्ट फेचिंग (Timer Fix)
    if is_teacher:
        # प्राथमिकता 1: वो रिक्वेस्ट ढूंढें जिसका सेशन अभी 'Active' है
        req = TradeRequest.query.filter_by(skill_id=skill.id, is_session_active=True).first()
        
        # प्राथमिकता 2: अगर कोई एक्टिव नहीं है, तो लेटेस्ट 'Accepted' रिक्वेस्ट उठाएं
        if not req:
            req = TradeRequest.query.filter(
                TradeRequest.skill_id == skill.id,
                TradeRequest.status == 'Accepted'
            ).order_by(TradeRequest.timestamp.desc()).first()
    else:
        # स्टूडेंट के लिए अपनी खुद की स्वीकृत रिक्वेस्ट देखें
        req = TradeRequest.query.filter_by(
            skill_id=skill.id, 
            sender_id=current_user.id,
            status='Accepted'
        ).first()

    # एक्सेस चेक
    if not is_teacher and not req: 
        flash("🔒 Access Denied: No active session found.", "error")
        return redirect(url_for('views.dashboard'))
    
    # 2. चैट मैसेज लोड करें
    messages = Message.query.filter_by(skill_id=skill.id).order_by(Message.timestamp.asc()).all()

    # 3. ✅ मैसेज डिस्प्ले लॉजिक (AttributeError Fix)
    # चूंकि Message मॉडल में 'user' रिलेशनशिप नहीं है, हम सीधे User टेबल से नाम निकालेंगे
    for msg in messages:
        sender_user = User.query.get(msg.sender_id)
        if msg.sender_id == skill.user_id:
            msg.sender_display = "Teacher 👨‍🏫"
        else:
            msg.sender_display = sender_user.first_name if sender_user else "Student"

    return render_template("classroom.html", 
                           skill=skill, 
                           user=current_user, 
                           messages=messages, 
                           is_teacher=is_teacher, 
                           req=req) # 'req' अब सही डेटा लेकर जाएगा

# ==========================================
# 🆕 ATTENDANCE HEARTBEAT (New Route)
# ==========================================
@views.route('/log-attendance/<int:req_id>', methods=['POST'])
@login_required
def log_attendance(req_id):
    req = TradeRequest.query.get_or_404(req_id)
    
    # सिर्फ वही स्टूडेंट अटेंडेंस लगा सकता है जिसने रिक्वेस्ट भेजी है
    if req.sender_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    # अगर क्लास चल रही है (Accepted status), तो टाइम बढ़ाओ
    if req.status == 'Accepted':
        # सेफ्टी चेक: अगर टाइम, टोटल टाइम से कम है तभी बढ़ाओ
        if req.attendance_minutes < req.skill.duration_minutes:
            req.attendance_minutes += 1
            db.session.commit()
            
        return jsonify({
            'status': 'success', 
            'current_minutes': req.attendance_minutes,
            'total_minutes': req.skill.duration_minutes
        })
    
    return jsonify({'status': 'ignored'})


# ==========================================
# 💰 COMPLETE SESSION & UNLOCK CERTIFICATE
# ==========================================
@views.route('/release-payment/<int:req_id>', methods=['POST'])
@login_required
def release_payment(req_id):
    """क्लास पूरी करने, पेमेंट रिलीज करने और प्रोफेशनल सर्टिफिकेट ID जनरेट करने का मास्टर रूट।"""
    req = TradeRequest.query.get_or_404(req_id)
    
    # सुरक्षा चेक: केवल वही छात्र पेमेंट रिलीज कर सकता है जिसने क्लास ली है
    if req.sender_id != current_user.id:
        flash("🚫 Unauthorized action.", "error")
        return redirect(url_for('views.dashboard'))

    # ✅ समय की जांच (Buffer Fix के साथ)
    if req.attendance_minutes >= (req.skill.duration_minutes - 1):
        try:
            # 1. टीचर को कॉइन्स ट्रांसफर करें
            teacher = User.query.get(req.receiver_id)
            if req.escrow_balance > 0:
                amount_earned = req.escrow_balance
                teacher.coins += amount_earned
                req.escrow_balance = 0
                
                # कॉइन हिस्ट्री में टीचर के लिए रिकॉर्ड जोड़ें
                new_log = CoinHistory(
                    user_id=teacher.id,
                    amount=amount_earned,
                    transaction_type=f"Earned from teaching '{req.skill.title}'"
                )
                db.session.add(new_log)

            # 2. 🔥 PROFESSIONAL CERTIFICATE ID जनरेट करें (Sync Fix)
            # यह फॉर्मेट ईमेल और गैलरी में 100% समानता पक्का करता है।
            if not req.certificate_no:
                import random
                cert_req_id = f"{req.id:03}" 
                random_num = random.randint(1000, 9999)
                date_suffix = datetime.datetime.now().strftime('%d%m%Y')
                # फॉर्मेट: GYANBARTER-CERT-005-4336/15022026
                req.certificate_no = f"GYANBARTER-CERT-{cert_req_id}-{random_num}/{date_suffix}"
            
            # 3. स्टेटस अपडेट करें
            req.status = 'Completed' 
            req.is_released = True
            req.is_session_active = False
            
            # 🔔 टीचर को नोटिफिकेशन भेजें
            notif_msg = f"💰 Success! You earned {amount_earned} coins for '{req.skill.title}'."
            new_notif = Notification(user_id=teacher.id, message=notif_msg)
            db.session.add(new_notif)

            # डेटाबेस में बदलाव पक्के करें ताकि ईमेल हेल्पर को सेव डेटा मिले
            db.session.commit()
            
            # 4. 📧 ईमेल भेजें (यह हेल्पर पक्का करेगा कि वही ID मेल में जाए जो ऊपर सेव हुई है)
            send_certificate_email(req)
            
            flash(f"🎉 Awesome! Class marked as Completed. Your Certificate ID: {req.certificate_no}", "success")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Payment Release Error: {e}")
            flash("An error occurred while releasing payment.", "error")
    else:
        flash("⚠️ Session time not finished. Please wait for the classroom timer.", "error")
        
    return redirect(url_for('views.dashboard'))


# =========================================================
# ✅ 8. SOCKET CHAT & RATINGS (Final with Teacher Symbol)
# =========================================================

@socketio.on('join')
def on_join(data): 
    join_room(str(data.get('room')))

@socketio.on('send_group_message')
def handle_group_message(data):
    msg, sid = data.get('msg'), str(data.get('skill_id'))
    if msg and sid:
        from .models import Skill # सर्कुलर इम्पोर्ट से बचने के लिए
        skill = Skill.query.get(int(sid))
        
        # 👨‍🏫 रीयल-टाइम डिस्प्ले लॉजिक: अगर सेंडर ही टीचर है तो लेबल बदलें
        if current_user.id == skill.user_id:
            display_name = "🧑🏻‍🏫 Teacher" # बोर्ड सिम्बल के साथ
        else:
            display_name = current_user.first_name

        # मैसेज डेटाबेस में सेव करना (sender_id का इस्तेमाल करते हुए)
        db.session.add(Message(
            content=msg, 
            sender_id=current_user.id, 
            skill_id=int(sid)
        ))
        db.session.commit()

        # 🚀 रीयल-टाइम अपडेट: अब स्टूडेंट को तुरंत 'Teacher 👨‍🏫' दिखेगा
        emit('receive_group_message', {
            'msg': msg, 
            'sender_name': display_name, # यहाँ नाम फिक्स कर दिया गया है
            'sender_id': current_user.id, 
            'timestamp': datetime.datetime.now().strftime('%H:%M')
        }, to=sid)


# =========================================================
# 🔒 9. ADMIN ANALYTICS (EXCLUDING ADMINS FROM STATS ✨)
# =========================================================

@views.route('/admin-analytics')
@login_required
def admin_analytics():
    # 👑 किंग लॉजिक: सिर्फ ये दो ईमेल ही डैशबोर्ड देख सकते हैं
    allowed_emails = ["gyanbarter@gmail.com", "arunbond149@gmail.com"]
    
    if current_user.email.strip().lower() not in allowed_emails:
        flash("Unauthorized access! High alert generated. 🚨", "error")
        return redirect(url_for('views.home'))
    
    # ✅ फ़िल्टर: सिर्फ उन लोगों को गिनें जो एडमिन नहीं हैं
    # इससे आपकी अपनी आईडी 'Total Users' में नहीं गिनी जाएगी।
    total_users_count = User.query.filter_by(is_admin=False).count()
    
    # ✅ लिस्ट में भी सिर्फ आम यूज़र्स को दिखाएँ, खुद को नहीं
    recent_members = User.query.filter_by(is_admin=False).order_by(User.date_joined.desc()).limit(10).all()
    
    data = {
        'total_users': total_users_count,
        'total_skills': Skill.query.count(),
        'total_coins': db.session.query(db.func.sum(User.coins)).scalar() or 0,
        # अगर आपके पास TradeRequest मॉडल है, तो इसे इस्तेमाल करें वरना 5 रहने दें
        'active_trades': TradeRequest.query.filter_by(status='Accepted').count() if 'TradeRequest' in globals() else 5,
        'recent_users': recent_members
    }
    
    return render_template("admin_analytics.html", user=current_user, **data)
# ==========================================
# ✅ UNIQUE USERNAME CHECK (API)
# ==========================================
@views.route('/check-username', methods=['POST'])
@login_required
def check_username():
    data = request.get_json()
    new_username = data.get('username', '').strip().lower()
    
    # अगर यूजरनेम खाली है
    if not new_username:
        return jsonify({'available': False})

    # अगर यूजरनेम खुद का है तो Available दिखाओ
    if new_username == current_user.username:
        return jsonify({'available': True})
        
    user_exists = User.query.filter_by(username=new_username).first()
    return jsonify({'available': not user_exists})

# ==========================================
# 👤 PROFILE UPDATE & SETTINGS
# ==========================================

@views.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        bio = request.form.get('bio')
        dob = request.form.get('dob')
        gender = request.form.get('gender')
        
        # 1. यूनिक यूजरनेम चेक (Duplicate Check)
        existing_user = User.query.filter_by(username=username).first()
        
        if existing_user and existing_user.id != current_user.id:
            flash("❌ This username is already taken! Please choose another.", "error")
        else:
            # 2. बेसिक डेटा अपडेट
            current_user.username = username
            current_user.bio = bio
            current_user.dob = dob
            current_user.gender = gender
            
            # 3. प्रोफाइल फोटो अपलोड लॉजिक
            if 'profile_pic' in request.files:
                file = request.files['profile_pic']
                if file and file.filename != '':
                    from werkzeug.utils import secure_filename
                    import os
                    
                    filename = secure_filename(f"user_{current_user.id}_{file.filename}")
                    # पक्का करें कि यह पाथ आपके फोल्डर स्ट्रक्चर से मेल खाता है
                    upload_path = os.path.join(current_app.root_path, 'static', 'uploads', filename)
                    file.save(upload_path)
                    current_user.profile_pic = filename

            db.session.commit()
            flash("✅ Profile updated successfully!", "success")
            
    return render_template("profile.html", user=current_user)

# ✅ नोटिफिकेशन मार्क रीड फंक्शन (Duplicate Error से बचने के लिए इसे यहीं रखें)
@views.route('/mark-read/<int:notif_id>', methods=['POST'])
@login_required
def mark_read(notif_id):
    notif = Notification.query.get_or_404(notif_id)
    if notif.user_id == current_user.id:
        notif.is_read = True
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 403
# ==========================================
# 🔍 USER SEARCH & PUBLIC PROFILES (INSTAGRAM STYLE)
# ==========================================

@views.route('/user/<string:username>')
@login_required
def public_profile(username):
    """किसी भी यूजर की पब्लिक प्रोफाइल दिखाने के लिए"""
    # डेटाबेस में यूजरनेम से यूजर को ढूंढें
    target_user = User.query.filter_by(username=username).first_or_404()
    
    # अगर यूजर खुद की प्रोफाइल देख रहा है, तो उसे उसके पर्सनल एडिट पेज पर भेज दें
    if target_user.id == current_user.id:
        return redirect(url_for('views.profile'))
        
    # दूसरों के लिए 'public_profile.html' दिखाएं (इसमें एडिट का विकल्प नहीं होगा)
    return render_template("public_profile.html", user=target_user)

@views.route('/search-users', methods=['GET'])
@login_required
def search_users():
    """यूजरनेम के जरिए लोगों को ढूंढने के लिए"""
    query = request.args.get('username', '').strip().lower()
    
    if query:
        # नाम से मिलते-जुलते सभी एक्टिव यूजर्स को ढूंढें
        users = User.query.filter(User.username.icontains(query)).all()
        return render_template("user_results.html", users=users, query=query)
    
    # अगर सर्च बॉक्स खाली है, तो वापस मार्केट पर भेज दें
    flash("Please enter a username to search.", "info")
    return redirect(url_for('views.market'))


# --- Updated Dashboard Logic ---
@views.route('/dashboard')
@login_required
def dashboard():
    """यूजर का मुख्य कंट्रोल पैनल: स्किल्स, रिक्वेस्ट और रिवॉर्ड्स को मैनेज करने के लिए।"""
    
    # 1️⃣ आपके द्वारा पोस्ट की गई स्किल्स (Latest First)
    my_skills = Skill.query.filter_by(
        user_id=current_user.id
    ).order_by(Skill.date_posted.desc()).all()

    # 2️⃣ आपको मिली हुई रिक्वेस्ट (Teacher View - Admissions के लिए)
    incoming_requests = TradeRequest.query.filter_by(
        receiver_id=current_user.id
    ).order_by(TradeRequest.timestamp.desc()).all()

    # 3️⃣ आपके द्वारा भेजी गई रिक्वेस्ट (Student View - Learning Journey के लिए)
    sent_requests = TradeRequest.query.filter_by(
        sender_id=current_user.id
    ).order_by(TradeRequest.timestamp.desc()).all()

    # 4️⃣ ✅ पूरा हो चुका सेशन डेटा (Certificate और रेटिंग के लिए)
    completed_sessions = [
        req for req in sent_requests 
        if req.status.lower() == "completed"
    ]

    # 5️⃣ रेंडर टेम्पलेट (सुनिश्चित करें कि फाइल का नाम 'dashboard.html' ही है)
    return render_template(
        "dashboard.html", 
        user=current_user,
        skills=my_skills,             # Table में दिखाने के लिए
        incoming_requests=incoming_requests, # Admissions के लिए
        sent_requests=sent_requests,         # Learning Journey के लिए
        completed_sessions=completed_sessions,
        now=datetime.datetime.now()   # Reward/Spin टाइमर के लिए
    )

# =========================================================
# 10. ERROR HANDLERS
# =========================================================

@views.app_errorhandler(404)
def page_not_found(e): return render_template('404.html', user=current_user), 404

@views.app_errorhandler(500)
def internal_server_error(e): return render_template('500.html', user=current_user), 500

# views.py के अंत में इसे अपडेट करें
from werkzeug.security import generate_password_hash
from flask_login import login_user

# ==========================================
# 📝 SIGN UP ROUTE (Error-Free Version)
# ==========================================
@views.route('/sign-up', methods=['GET', 'POST']) # ✅ '@auth' को '@views' में बदल दिया गया है
def sign_up():
    if request.method == 'POST':
        # 1. डेटा प्राप्त करें
        email = request.form.get('email')
        firstName = request.form.get('firstName')
        lastName = request.form.get('lastName')
        username = request.form.get('username') 
        password = request.form.get('password')

        # 2. 🛡️ Safety Check: 'username' को Null होने से बचाएं
        if not username and email:
            username = email.split('@')[0]

        # 3. वैलिडेशन
        user_exists = User.query.filter_by(email=email).first()
        if user_exists:
            flash('Email already exists.', category='error')
        elif len(password) < 7:
            flash('Password is too short.', category='error')
        else:
            # 4. ✅ नया यूजर बनाएँ (username के साथ)
            new_user = User(
                email=email,
                first_name=firstName,
                last_name=lastName,
                username=username, # ✅ डेटाबेस की आवश्यकता पूरी हुई
                password=generate_password_hash(password, method='pbkdf2:sha256')
            )
            
            try:
                db.session.add(new_user)
                db.session.commit()
                login_user(new_user, remember=True)
                flash('Account created successfully!', category='success')
                return redirect(url_for('views.dashboard'))
            except Exception as e:
                db.session.rollback()
                flash('Database error. Please try again.', category='error')
                print(f"Error: {e}")

    return render_template("sign_up.html", user=current_user)


import datetime  # ✅ यह इम्पोर्ट होना ज़रूरी है

# ==========================================
# 🔗 JOIN MEETING (Activates Session Timer)
# ==========================================
@views.route('/join-meeting/<int:req_id>')
@login_required
def join_external_meeting(req_id):
    # डेटाबेस से रिक्वेस्ट निकालें
    req = TradeRequest.query.get_or_404(req_id)
    
    # ✅ सुरक्षा चेक: सिर्फ छात्र ही सेशन एक्टिवेट कर सकता है
    if req.sender_id != current_user.id:
        flash("Unauthorized access: Only the student can start the session.", "error")
        return redirect(url_for('views.dashboard'))

    # ✅ UTC समय के साथ सेशन चालू करें
    if not req.is_session_active:
        req.is_session_active = True
        # utcnow() इस्तेमाल करें ताकि पूरी दुनिया के लिए समय एक समान रहे
        req.session_start_time = datetime.datetime.utcnow() 
        db.session.commit()
        print(f"🚀 Session Sync: Request {req_id} started at UTC {req.session_start_time}")
    
    # मीटिंग लिंक पर रिडायरेक्ट करें
    if req.skill.meeting_link:
        return redirect(req.skill.meeting_link)
    else:
        flash("Meeting link missing. Please contact the teacher.", "error")
        return redirect(url_for('views.classroom', skill_id=req.skill_id))
    
 # ==========================================
# 🚪 LEAVE CLASS & AUTO-COMPLETE (Master Function)
# ==========================================
@views.route('/leave-class/<int:req_id>', methods=['POST'])
@login_required
def leave_class(req_id):
    req = TradeRequest.query.get_or_404(req_id)
    
    # 1. सुरक्षा चेक
    if current_user.id not in [req.sender_id, req.receiver_id]:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    if req.is_session_active:
        # समय की गणना
        if req.session_start_time:
            duration_delta = datetime.datetime.utcnow() - req.session_start_time
            total_mins_spent = duration_delta.total_seconds() / 60
            required_mins = req.skill.duration_minutes
            
            # ---------------------------------------------------------
            # ✅ CASE A: SUCCESS (Time Completed)
            # ---------------------------------------------------------
            if current_user.id == req.sender_id and total_mins_spent >= (required_mins - 1): # 1 min buffer
                try:
                    teacher = User.query.get(req.receiver_id)
                    
                    # 1. पैसे ट्रांसफर करें
                    if req.escrow_balance > 0:
                        teacher.coins += req.escrow_balance
                        db.session.add(CoinHistory(user_id=teacher.id, amount=req.escrow_balance, 
                                                 transaction_type=f"Earned: {req.skill.title}"))
                        req.escrow_balance = 0
                    
                    # 2. स्टेटस को 'Completed' करें
                    req.status = 'Completed'
                    req.is_released = True
                    req.is_session_active = False
                    
                    db.session.commit()
                    
                    # 🔥 3. ऑटोमैटिक ईमेल भेजें (New Feature)
                    print(f"📧 Sending certificate to {current_user.email}...")
                    send_certificate_email(req) 

                    # सॉकेट सिग्नल
                    socketio.emit('session_ended', {'req_id': req_id}, room=str(req.skill_id))
                    
                    return jsonify({
                        "status": "success", 
                        "message": "Class Completed! Certificate has been emailed to you."
                    })
                    
                except Exception as e:
                    db.session.rollback()
                    print(f"Error in leave_class: {e}")
                    return jsonify({"status": "error", "message": "Sync Error. Please try again."})

            # ---------------------------------------------------------
            # ❌ CASE B: PENALTY (Left Early)
            # ---------------------------------------------------------
            elif current_user.id == req.sender_id and total_mins_spent < required_mins:
                deducted_penalty = 5
                student = User.query.get(req.sender_id)
                teacher = User.query.get(req.receiver_id)
                
                if student.coins >= deducted_penalty:
                    student.coins -= deducted_penalty
                    teacher.coins += deducted_penalty
                    
                    db.session.add(CoinHistory(user_id=student.id, amount=-deducted_penalty, transaction_type="Penalty: Left Early"))
                    db.session.add(CoinHistory(user_id=teacher.id, amount=deducted_penalty, transaction_type="Bonus: Student Left Early"))
        
        # सेशन बंद करें (Status 'Accepted' ही रहेगा)
        req.is_session_active = False
        db.session.commit()
        socketio.emit('session_ended', {'req_id': req_id}, room=str(req.skill_id))
        
        return jsonify({"status": "success", "message": "Session Exited (Incomplete)."})

    return jsonify({"status": "error", "message": "Session already closed."})

import os
from flask import current_app # पक्का करें कि ये import ऊपर हो

# ==========================================
# 📄 DOWNLOAD CERTIFICATE (Synced & Fixed)
# ==========================================
@views.route('/download-certificate/<int:req_id>')
@login_required
def download_certificate(req_id):
    """सर्टिफिकेट को डेटाबेस की यूनिक ID के साथ PDF में डाउनलोड करने का मास्टर रूट।"""
    req = TradeRequest.query.get_or_404(req_id)

    # 1. 🔐 Security Check: केवल छात्र ही अपना सर्टिफिकेट ले सकता है
    if req.sender_id != current_user.id:
        flash("🚫 Unauthorized Access.", "error")
        return redirect(url_for('views.dashboard'))

    # 2. ✅ Status Check: सिर्फ 'Completed' होने पर ही डाउनलोड की अनुमति दें
    if req.status != 'Completed' and not req.is_released:
        flash("⏳ Certificate is not ready. Please complete the class payment first.", "error")
        return redirect(url_for('views.dashboard'))

    # 3. 🖼️ Image Paths Fix (Playwright Absolute Path Logic)
    try:
        static_folder = os.path.join(current_app.root_path, 'static')
        
        # लोगो और सील का पाथ (Playwright को file:/// फॉर्मेट चाहिए होता है)
        logo_path = os.path.join(static_folder, 'img', 'logo.png')
        logo_url = "file:///" + logo_path.replace("\\", "/")
        
        seal_path = os.path.join(static_folder, 'img', 'seal.png')
        seal_url = "file:///" + seal_path.replace("\\", "/")
    except Exception as e:
        print(f"Path Handling Error: {e}")
        logo_url = ""
        seal_url = ""

    # 4. 📜 Data Sync (Using Persistent Database ID)
    # ✅ 'certificate_id' अब सीधे 'req.certificate_no' से आ रहा है
    data = {
        "student_name": f"{req.sender.first_name} {req.sender.last_name or ''}".strip(),
        "teacher_name": req.receiver.first_name,
        "skill_title": req.skill.title,
        "completion_date": req.timestamp.strftime("%d %b, %Y"),
        "certificate_id": req.certificate_no if req.certificate_no else f"GB-{req.id}-NEW", 
        "current_year": datetime.datetime.now().year,
        "logo_url": logo_url,
        "seal_url": seal_url
    }

    # HTML टेम्पलेट को रेंडर करें
    rendered_html = render_template("certificate_template.html", **data)

    # 5. 🖨️ PDF Generation (Using Playwright)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            
            # नेटवर्क शांत होने का इंतज़ार करें ताकि इमेज रेंडर हो सकें
            page.set_content(rendered_html, wait_until="networkidle")
            
            pdf = page.pdf(
                format="A4",
                landscape=True,  # प्रोफेशनल लुक के लिए लैंडस्केप
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"}
            )
            browser.close()

        # ब्राउज़र को PDF फाइल भेजें
        response = make_response(pdf)
        response.headers["Content-Type"] = "application/pdf"
        
        # फाइल का नाम अब यूनिक सर्टिफिकेट आईडी के साथ होगा
        filename = f"GyanBarter_Cert_{data['certificate_id']}.pdf"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        
        return response

    except Exception as e:
        print(f"❌ PDF Engine Error: {e}")
        flash("PDF Generator is having issues. Please try again later.", "error")
        return redirect(url_for("views.dashboard"))

# ==========================================
# ⭐ SUBMIT RATING (Final Fixed & Notified)
# ==========================================
@views.route('/submit-rating/<int:req_id>', methods=["POST"])
@login_required
def submit_rating(req_id):
    """छात्रों द्वारा टीचर को रेटिंग और फीडबैक देने के लिए मास्टर रूट।"""
    
    # 1. डेटाबेस से रिक्वेस्ट निकालें
    req = TradeRequest.query.get_or_404(req_id)

    # 2. 🔐 सुरक्षा चेक: सिर्फ वही छात्र रेटिंग दे सकता है जिसने क्लास ली हो
    if req.sender_id != current_user.id:
        flash("🚫 Unauthorized access.", "error")
        return redirect(url_for("views.dashboard"))

    # 3. ✅ स्टेटस चेक: सिर्फ 'Completed' क्लास पर ही रेटिंग दी जा सकती है
    if req.status != "Completed":
        flash("⚠️ Class is not completed yet.", "error")
        return redirect(url_for("views.dashboard"))

    # 4. ✅ डुप्लीकेट चेक: पक्का करें कि एक छात्र एक क्लास के लिए एक ही बार रेटिंग दे
    existing_rating = Rating.query.filter_by(request_id=req.id).first()
    if existing_rating:
        flash("ℹ️ You have already rated this session.", "info")
        return redirect(url_for("views.dashboard"))

    # 5. फॉर्म से डेटा प्राप्त करें
    rating_value = request.form.get("rating")
    review = request.form.get("review")

    # 6. वैलिडेशन: पक्का करें कि स्टार सिलेक्ट किया गया है
    if not rating_value:
        flash("⚠️ Please select a star rating.", "error")
        return redirect(url_for("views.dashboard"))

    try:
        # 7. रेटिंग को डेटाबेस में सुरक्षित करें
        new_rating = Rating(
            rating=int(rating_value),
            review=review,
            sender_id=current_user.id,
            receiver_id=req.receiver_id,
            request_id=req.id
        )
        db.session.add(new_rating)

        # 8. 🔔 टीचर को नोटिफिकेशन भेजें (Added Feature)
        # इससे टीचर को तुरंत पता चलेगा कि उसे नया रिव्यू मिला है।
        msg = f"🌟 New Feedback: {current_user.first_name} gave you {rating_value} stars for '{req.skill.title}'!"
        new_notif = Notification(
            user_id=req.receiver_id,
            message=msg,
            is_read=False
        )
        db.session.add(new_notif)

        # 9. सभी बदलावों को सेव करें
        db.session.commit()
        flash("⭐ Rating submitted successfully! Thank you.", "success")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Rating Database Error: {e}")
        flash("Something went wrong while saving rating.", "error")

    # 'BuildError' से बचने के लिए सही फंक्शन का उपयोग
    return redirect(url_for("views.dashboard"))

# ==========================================
# 📂 UPDATE CLASS RESOURCES (Meeting & Drive Links)
# ==========================================
@views.route('/update-resources/<int:skill_id>', methods=['POST'])
@login_required
def update_class_resources(skill_id):
    skill = Skill.query.get_or_404(skill_id)
    
    # सुरक्षा चेक: सिर्फ टीचर ही लिंक बदल सकता है
    if skill.user_id != current_user.id:
        flash("Unauthorized access.", "error")
        return redirect(url_for('views.dashboard'))

    action = request.form.get('action')

    # 1. मीटिंग लिंक (Zoom/Google Meet) को संभालें
    if action == 'save_zoom':
        skill.meeting_link = request.form.get('meeting_link')
        # सॉकेट के ज़रिए स्टूडेंट को तुरंत बताएं
        socketio.emit('resource_updated', {'type': 'zoom', 'link': skill.meeting_link}, room=str(skill_id))
    elif action == 'delete_zoom':
        skill.meeting_link = None
        socketio.emit('resource_updated', {'type': 'zoom', 'link': None}, room=str(skill_id))

    # 2. ड्राइव लिंक (Notes/PDF) को संभालें
    elif action in ['save_drive', 'delete_drive']:
        # एक्टिव सेशन ढूंढें ताकि लिंक सही स्टूडेंट को मिले
        req = TradeRequest.query.filter_by(skill_id=skill_id, is_session_active=True).first()
        if req:
            if action == 'save_drive':
                req.drive_link = request.form.get('drive_link')
            else:
                req.drive_link = None
            # सॉकेट के ज़रिए स्टूडेंट के डैशबोर्ड पर अपडेट भेजें
            socketio.emit('resource_updated', {'type': 'drive', 'link': req.drive_link}, room=str(skill_id))

    try:
        db.session.commit()
        flash("Resources updated successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash("Database Error: Could not update links.", "error")

    return redirect(url_for('views.classroom', skill_id=skill_id))

# ==========================================
# 🔄 DASHBOARD REAL-TIME STATUS CHECK (Final)
# ==========================================
@views.route('/api/check-status/<int:req_id>')
@login_required
def check_status(req_id):
    req = TradeRequest.query.get(req_id)
    if not req:
        return jsonify({'error': 'Not found'}), 404
        
    # असली टीचर द्वारा सेट किए गए 'duration_minutes' से चेक करेगा
    required = req.skill.duration_minutes
    current = req.attendance_minutes
    
    # ✅ 1 मिनट का बफ़र (Buffer): 35 में से 34 मिनट पर भी अनलॉक कर दो
    is_unlocked = current >= (required - 1)
    
    # प्रोग्रेस बार के लिए वैल्यू (अधिकतम 100%)
    progress_val = (current / required) * 100 if required > 0 else 0
    
    return jsonify({
        'current': current,
        'required': required,
        'left': max(0, required - current),
        'is_unlocked': is_unlocked,
        'progress': min(100, round(progress_val)),
        'status': req.status  # यह डैशबोर्ड रिफ्रेश ट्रिगर के लिए ज़रूरी है
    })
    
    
# ==========================================
# 🏅 MY CERTIFICATES PAGE (Synced & Unique)
# ==========================================
@views.route('/my-certificates')
@login_required
def my_certificates():
    """छात्र द्वारा पूरे किए गए सभी सेशन्स और उनके यूनिक सर्टिफिकेट्स दिखाने के लिए मास्टर रूट।"""
    
    # 1. डेटाबेस से वो सेशन्स निकालें जहाँ यूजर छात्र (sender) है
    # और स्टेटस 'Completed' है या पेमेंट रिलीज हो चुकी है।
    completed_sessions = TradeRequest.query.filter(
        TradeRequest.sender_id == current_user.id,
        (TradeRequest.status == 'Completed') | (TradeRequest.is_released == True)
    ).order_by(TradeRequest.timestamp.desc()).all()
    
    # ✅ नोट: 'completed_sessions' में अब 'certificate_no' भी शामिल होगा
    # जो 'release_payment' के दौरान जनरेट हुआ था।
    # 2. डेटा को गैलरी टेम्पलेट में भेजें
    return render_template(
        "my_certificates.html", 
        sessions=completed_sessions, 
        user=current_user
    )


# ==========================================
# 📥 INCOMING REQUESTS PAGE (Final Master)
# ==========================================
@views.route('/incoming-requests')
@login_required
def incoming_requests():
    """टीचर के लिए पेंडिंग छात्र रिक्वेस्ट देखने का पेज।"""
    # डेटाबेस से सिर्फ वो रिक्वेस्ट निकालें जिनका स्टेटस 'Pending' है
    requests = TradeRequest.query.filter_by(
        receiver_id=current_user.id, 
        status='Pending'
    ).order_by(TradeRequest.timestamp.desc()).all()
    
    # 'requests' को टेम्पलेट में भेजें
    return render_template("incoming_requests.html", user=current_user, requests=requests)

# ==========================================
# 🔔 MARK ALL NOTIFICATIONS READ
# ==========================================
@views.route('/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    try:
        Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
        db.session.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

import base64
import random
import os
from flask_mail import Message as EmailMessage # ✅ सही इंपोर्ट सुनिश्चित करें

def send_certificate_email(req):
    """सर्टिफिकेट जनरेट करके छात्र को मेल भेजने वाला मास्टर हेल्पर।"""
    try:
        from .models import User 
        from . import db, mail
        
        # 1. डेटा तैयार करें
        student = User.query.get(req.sender_id)
        
        # ✅ SYNC FIX: अगर ID पहले से नहीं है, तभी नया बनाएँ, वरना पुराना इस्तेमाल करें
        if not req.certificate_no:
            cert_req_id = f"{req.id:03}" 
            random_num = random.randint(1000, 9999)
            date_suffix = datetime.datetime.now().strftime('%d%m%Y')
            req.certificate_no = f"GYANBARTER-CERT-{cert_req_id}-{random_num}/{date_suffix}"
            db.session.commit() # तुरंत सेव करें ताकि गैलरी में भी यही दिखे

        professional_cert_id = req.certificate_no

        # 2. LOGO BASE64 LOGIC (PDF में इमेज दिखाने के लिए)
        logo_path = os.path.join(current_app.root_path, 'static', 'img', 'logo.png') 
        logo_base64 = ""
        if os.path.exists(logo_path):
            with open(logo_path, "rb") as img_file:
                b64_string = base64.b64encode(img_file.read()).decode('utf-8')
                logo_base64 = f"data:image/png;base64,{b64_string}"

        data = {
            'student_name': f"{student.first_name} {student.last_name or ''}".strip(),
            'skill_title': req.skill.title,
            'completion_date': req.timestamp.strftime('%d %b, %Y') if req.timestamp else datetime.datetime.now().strftime('%d %b, %Y'),
            'current_year': datetime.datetime.now().year,
            'certificate_id': professional_cert_id, 
            'logo_base64': logo_base64 
        }

        # 3. PDF GENERATION (Playwright)
        rendered = render_template('certificate_template.html', **data)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content(rendered, wait_until="networkidle") 
            pdf_bytes = page.pdf(format="A4", landscape=True, print_background=True)
            browser.close()

        # 4. PROFESSIONAL EMAIL SENDING
        msg = EmailMessage(
            subject=f"🎓 Certificate of Excellence: {req.skill.title}",
            recipients=[student.email],
            body=(
                f"Dear {student.first_name},\n\n"
                f"Congratulations! You have successfully completed the session on '{req.skill.title}'.\n\n"
                f"Your unique Certificate ID is: {professional_cert_id}\n"
                f"You can view and download this anytime from your Achievement Gallery on GyanBarter.\n\n"
                f"Best Regards,\n"
                f"Team GyanBarter"
            )
        )
        msg.attach(f"GyanBarter_Certificate_{req.id}.pdf", "application/pdf", pdf_bytes)
        mail.send(msg)
        
        print(f"✅ Success! ID {professional_cert_id} sent to {student.email}")
        return True

    except Exception as e:
        print(f"❌ Email Sync Error: {e}")
        return False


# ✅ 1. 'My Skills' बटन के लिए (BuildError यहाँ से ठीक होगा)
@views.route('/my-skills')
@login_required
def my_skills():
    """यूजर द्वारा पोस्ट की गई सभी स्किल्स को मैनेज करने का पेज"""
    user_skills = Skill.query.filter_by(user_id=current_user.id).order_by(Skill.date_posted.desc()).all()
    return render_template("my_skills.html", user=current_user, skills=user_skills)


# ✅ 1. 'Avg Rating' कार्ड के लिए (इसी की वजह से अभी एरर आ रहा है)
@views.route('/my-reviews')
@login_required
def my_reviews():
    """छात्रों द्वारा टीचर (current_user) को दी गई रेटिंग और रिव्यूज का पेज"""
    # डेटाबेस से वो रेटिंग निकालें जो टीचर को मिली हैं
    reviews = Rating.query.filter_by(receiver_id=current_user.id).order_by(Rating.timestamp.desc()).all()
    return render_template("my_reviews.html", user=current_user, reviews=reviews)


# ✅ Terms of Use रूट
@views.route('/terms')
def terms():
    return render_template("terms.html", user=current_user)

# ✅ Privacy Policy रूट
@views.route('/privacy')
def privacy():
    return render_template("privacy.html", user=current_user)

from flask import abort, flash, redirect, url_for
from flask_login import login_required, current_user
from .models import User
from . import db

@views.route('/admin/user-action/<int:user_id>/<action>', methods=['POST'])
@login_required
def user_management_action(user_id, action):
    # 🛡️ LAYER 1: क्या लॉगिन किया हुआ बंदा एडमिन है?
    if not current_user.is_admin:
        # अगर कोई नॉर्मल यूजर है, तो उसे 'Forbidden' एरर दिखाओ
        abort(403) 

    # 🛡️ LAYER 2: क्या यह 'gyanbarter' वाली खास ईमेल है? (एक्स्ट्रा सुरक्षा)
    allowed_admins = ["gyanbarter@gmail.com", "arunbond149@gmail.com"]
    if current_user.email.strip().lower() not in allowed_admins:
        flash("You do not have permission to modify user accounts.", "error")
        return redirect(url_for('views.home'))

    target_user = User.query.get_or_404(user_id)

    # 🚫 SELF-PROTECTION: एडमिन खुद पर हमला नहीं कर सकता
    if target_user.id == current_user.id:
        flash("System Security: Admin cannot ban or delete themselves!", "error")
        return redirect(url_for('views.admin_analytics'))

    # ✅ ACTIONS: अब जो एडमिन चाहे वो करे
    if action == 'active':
        target_user.account_status = 'Active'
        flash(f"{target_user.first_name} is now Active! ✅", "success")
    
    elif action == 'suspend':
        target_user.account_status = 'Suspended'
        flash(f"{target_user.first_name} has been Suspended. ⚠️", "warning")
    
    elif action == 'block':
        target_user.account_status = 'Blocked'
        flash(f"{target_user.first_name} has been Banned. 🚫", "error")
    
    elif action == 'delete':
        db.session.delete(target_user)
        db.session.commit()
        flash("Account Deleted Permanently!", "success")
        return redirect(url_for('views.admin_analytics'))

    db.session.commit()
    return redirect(url_for('views.admin_analytics'))