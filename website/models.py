from . import db
from flask_login import UserMixin
from sqlalchemy.sql import func
import datetime
import hashlib

# ==========================================
# 1. COIN HISTORY MODEL (Transaction Logs)
# ==========================================
class CoinHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    transaction_type = db.Column(db.String(150), nullable=False) # e.g., 'Earned', 'Spent', 'Bonus'
    timestamp = db.Column(db.DateTime(timezone=True), default=func.now())

    def __repr__(self):
        return f"<CoinHistory {self.amount} - {self.transaction_type}>"

# ==========================================
# 2. NOTIFICATION MODEL (User Alerts)
# ==========================================
class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=func.now())

# ==========================================
# 3. RATING MODEL (Peer Reviews)
# ==========================================
class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)
    review = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime(timezone=True), default=func.now())

    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    request_id = db.Column(db.Integer, db.ForeignKey("trade_request.id"), nullable=False)

    # ✅ Fix: Overlaps fix maintained to prevent terminal warnings
    reviewer = db.relationship("User", foreign_keys=[sender_id], backref="reviews_given")
    sender = db.relationship("User", foreign_keys=[sender_id], overlaps="reviewer,reviews_given")
    teacher = db.relationship("User", foreign_keys=[receiver_id], backref="reviews_received")
    request = db.relationship("TradeRequest", foreign_keys=[request_id], overlaps="ratings,request_ref")

    def __repr__(self):
        return f"<Rating {self.rating}>"

# ==========================================
# 4. USER MODEL (Master User Profile)
# ==========================================
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(150), nullable=False)
    last_name = db.Column(db.String(150), nullable=True)
    username = db.Column(db.String(150), unique=True, nullable=False)

    # 👑 Admin & Security Gates
    is_admin = db.Column(db.Boolean, default=False)
    account_status = db.Column(db.String(50), default="Active") # Active, Suspended, Blocked
    status_reason = db.Column(db.String(255), nullable=True)
    coins = db.Column(db.Integer, default=50)
    
    reset_token = db.Column(db.String(100), nullable=True)
    token_expiration = db.Column(db.DateTime, nullable=True)

    # 🎡 Gamification & Streak Features
    last_checkin_date = db.Column(db.Date, nullable=True)
    checkin_streak = db.Column(db.Integer, default=0)
    last_spin_date = db.Column(db.DateTime, nullable=True)
    spin_coins = db.Column(db.Integer, default=0)
    spin_coins_expiry = db.Column(db.DateTime, nullable=True)
    
    is_profile_complete = db.Column(db.Boolean, default=False)
    profile_pic = db.Column(db.String(255), default='default.png')
    date_joined = db.Column(db.DateTime(timezone=True), default=func.now())

    # Relationships
    skills = db.relationship("Skill", backref="author", cascade="all, delete-orphan", lazy=True)
    sent_requests = db.relationship("TradeRequest", foreign_keys="TradeRequest.sender_id", backref="sender", cascade="all, delete-orphan")
    received_requests = db.relationship("TradeRequest", foreign_keys="TradeRequest.receiver_id", backref="receiver", cascade="all, delete-orphan")
    coin_logs = db.relationship("CoinHistory", backref="owner", cascade="all, delete-orphan", lazy=True)
    notifications = db.relationship("Notification", backref="user", cascade="all, delete-orphan", lazy=True)

    def get_profile_image(self):
        """यूजर की अपलोड की हुई फोटो या नाम के पहले अक्षर (Capital) वाली फोटो लौटाता है।"""
        if self.profile_pic and self.profile_pic != 'default.png' and 'http' not in self.profile_pic:
            return f"/static/uploads/{self.profile_pic}"
        
        if self.profile_pic and 'http' in self.profile_pic:
            return self.profile_pic
            
        name_initial = self.first_name[0].upper() if self.first_name else "G"
        return f"https://ui-avatars.com/api/?name={name_initial}&background=random&color=fff&size=128&bold=true"

    def get_avg_rating(self):
        ratings = self.reviews_received
        if not ratings: return 0.0
        return round(sum(r.rating for r in ratings) / len(ratings), 1)

    def get_review_count(self):
        return len(self.reviews_received) if self.reviews_received else 0
        
    def generate_avatar_url(self):
        return self.get_profile_image()

    def __repr__(self):
        return f"<User {self.email}>"

# ==========================================
# 5. SKILL MODEL (Classroom Listings)
# ==========================================
class Skill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(100), default="General")
    difficulty_level = db.Column(db.String(100), default="Beginner (Level 1)")
    cost = db.Column(db.Integer, default=10)
    duration_minutes = db.Column(db.Integer, default=60)
    duration = db.Column(db.String(50), default='1 Hour')
    
    date_posted = db.Column(db.DateTime(timezone=True), default=func.now())
    meeting_link = db.Column(db.String(500))
    session_start = db.Column(db.DateTime)
    expiry_date = db.Column(db.DateTime)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    
    requests = db.relationship("TradeRequest", backref="skill", cascade="all, delete-orphan", lazy=True)
    messages = db.relationship("Message", backref="skill_ref", cascade="all, delete-orphan", lazy=True)

# ==========================================
# 6. TRADE REQUEST MODEL (Enrollments & Escrow)
# ==========================================
class TradeRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default="Pending") # Pending, Accepted, Completed, Rejected
    timestamp = db.Column(db.DateTime(timezone=True), default=func.now())
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skill.id"), nullable=False)

    escrow_balance = db.Column(db.Integer, default=0)
    is_released = db.Column(db.Boolean, default=False)
    is_session_active = db.Column(db.Boolean, default=False)
    session_start_time = db.Column(db.DateTime)
    session_end_time = db.Column(db.DateTime)
    attendance_minutes = db.Column(db.Integer, default=0)
    zoom_link = db.Column(db.String(500))
    drive_link = db.Column(db.String(500))
    certificate_no = db.Column(db.String(100), unique=True, nullable=True) 

    # ✅ Overlaps fix for cleaner relationships
    ratings = db.relationship("Rating", backref=db.backref("request_ref", overlaps="request"), cascade="all, delete-orphan", lazy=True, overlaps="request")
    messages = db.relationship("Message", backref="request_ref", cascade="all, delete-orphan", lazy=True)

    def get_user_rating(self, user_id):
        from .models import Rating 
        return Rating.query.filter_by(request_id=self.id, sender_id=user_id).first()

# ==========================================
# 7. MESSAGE MODEL (Classroom Chat)
# ==========================================
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=func.now())
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skill.id"), nullable=False)
    request_id = db.Column(db.Integer, db.ForeignKey("trade_request.id"))
    sender_display = db.Column(db.String(150))