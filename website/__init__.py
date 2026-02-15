from flask import Flask, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_mail import Mail
from authlib.integrations.flask_client import OAuth
from flask_socketio import SocketIO
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
import pymysql
import os

# --- 1. सिस्टम सेटअप ---
pymysql.install_as_MySQLdb()
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1' # लोकल टेस्टिंग के लिए

# ग्लोबल एक्सटेंशन
db = SQLAlchemy()
mail = Mail()
oauth = OAuth()
login_manager = LoginManager()
socketio = SocketIO()

# 🔴 सिस्टम सेटिंग्स (मालिक का ईमेल)
DB_NAME = "gyanbarter_db"
OWNER_EMAIL = "gyanbarter@gmail.com"

# ==================================================================
# 2. HIGH-SECURITY ADMIN CLASSES (System Owner Only)
# ==================================================================

class MyAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        if not current_user.is_authenticated or current_user.email != OWNER_EMAIL:
            flash("🔒 Access Denied: System Owner Access Only.", "error")
            return redirect(url_for('views.dashboard'))
        
        from .models import User, Skill, CoinHistory
        return self.render(
            'admin/index.html',  
            users_count=User.query.count(),
            skills_count=Skill.query.count(),
            coins_count=CoinHistory.query.count()
        )

    def is_accessible(self):
        return current_user.is_authenticated and current_user.email == OWNER_EMAIL

class SecureModelView(ModelView):
    can_delete = True 
    can_export = True
    def is_accessible(self):
        return current_user.is_authenticated and current_user.email == OWNER_EMAIL
    
    def inaccessible_callback(self, name, **kwargs):
        flash("Unauthorized! Owner access only.", "error")
        return redirect(url_for('auth.login'))

class TradeRequestView(SecureModelView):
    column_list = ['id', 'status', 'certificate_no', 'sender_id', 'receiver_id', 'is_released', 'timestamp']
    column_searchable_list = ['id', 'certificate_no', 'status']
    column_labels = {'certificate_no': 'Unique Certificate ID', 'is_released': 'Payment Released'}

class UserView(SecureModelView):
    column_list = ['id', 'email', 'username', 'first_name', 'coins', 'account_status', 'is_admin']
    column_editable_list = ['account_status', 'coins'] 
    form_columns = ['email', 'first_name', 'last_name', 'username', 'coins', 'account_status', 'status_reason']
    column_exclude_list = ['password']
    column_searchable_list = ['email', 'username', 'first_name']

# ------------------------------------------------------------------
# 3. ADMIN INITIALIZATION
# ------------------------------------------------------------------
admin = Admin(name='GyanBarter Admin', index_view=MyAdminIndexView(), endpoint='admin', url='/admin')

# ------------------------------------------------------------------
# 4. CREATE APP FACTORY
# ------------------------------------------------------------------

def create_app():
    app = Flask(__name__)
    
    # कोर कॉन्फ़िगरेशन
    app.config['SECRET_KEY'] = 'gyanbarter_secret_key_arun_bhai'
    app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://root:@localhost/{DB_NAME}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # अपलोड फोल्डर सेटअप
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    # 📧 मेल कॉन्फ़िगरेशन
    app.config.update(
        MAIL_SERVER='smtp.gmail.com',
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME=OWNER_EMAIL,
        MAIL_PASSWORD='usyr qtvs tupf ubmq', 
        MAIL_DEFAULT_SENDER=('GyanBarter Team', OWNER_EMAIL)
    )

    # एक्सटेंशन शुरू करें
    db.init_app(app)
    mail.init_app(app)
    oauth.init_app(app)
    login_manager.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*")
    admin.init_app(app)

    # 🚀 OAuth रजिस्ट्रेशन (Google & Facebook)
    oauth.register(
        name='google',
        client_id='1052405831260-9bm4sms3rvblb38k03dnngt04q8s736k.apps.googleusercontent.com',
        client_secret='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',
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )
    oauth.register(
        name='facebook',
        client_id='26169727362652327', 
        client_secret='e13a345b1270d2dba280617af65e2ba7',
        api_base_url='https://graph.facebook.com/',
        access_token_url='https://graph.facebook.com/oauth/access_token',
        authorize_url='https://www.facebook.com/dialog/oauth',
        client_kwargs={'scope': 'email public_profile'},
    )

    # ब्लूप्रिंट्स
    from .views import views
    from .auth import auth
    app.register_blueprint(views, url_prefix='/')
    app.register_blueprint(auth, url_prefix='/')

    # ✅ नोटिफिकेशन बेल लॉजिक (GLOBAL CONTEXT PROCESSOR)
    # यह हर पेज पर नोटिफिकेशन डेटा पहुंचाएगा
    @app.context_processor
    def inject_notifications():
        from .models import Notification
        if current_user.is_authenticated:
            unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            recent_notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.timestamp.desc()).limit(5).all()
            return dict(unread_count=unread_count, recent_notifs=recent_notifs)
        return dict(unread_count=0, recent_notifs=[])

    # एडमिन व्यू रजिस्ट्रेशन
    from .models import User, Skill, TradeRequest, CoinHistory, Rating, Notification
    if len(admin._views) <= 1: 
        admin.add_view(UserView(User, db.session, name="User Control", endpoint="users_m"))
        admin.add_view(SecureModelView(Skill, db.session, name="Skill Audit", endpoint="skills_m"))
        admin.add_view(TradeRequestView(TradeRequest, db.session, name="Certificate & Trades", endpoint="requests_m"))
        admin.add_view(SecureModelView(Notification, db.session, name="Notifications", endpoint="notifs_m"))

    login_manager.login_view = 'auth.login'
    
    @login_manager.user_loader
    def load_user(id):
        return db.session.get(User, int(id))

    with app.app_context():
        db.create_all() 
        print("✅ GyanBarter Command Center Active: Notifications & Social Login Ready.")

    return app