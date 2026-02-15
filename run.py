import jinja2

# 1. Jinja2 Pass Context Fix (For older dependencies)
if not hasattr(jinja2, 'contextfunction'):
    jinja2.contextfunction = jinja2.pass_context

# 2. SQLAlchemy Association Proxy Fix
# Flask-Admin compatibility fix
import sqlalchemy.ext.associationproxy
if not hasattr(sqlalchemy.ext.associationproxy, 'ASSOCIATION_PROXY'):
    from sqlalchemy.ext.associationproxy import AssociationProxy
    sqlalchemy.ext.associationproxy.ASSOCIATION_PROXY = AssociationProxy

from website import create_app, socketio

app = create_app()

if __name__ == '__main__':
    # ✅ Development Mode ON (debug=True)
    # Ab hum wapas coding mode mein hain.
    # Agar koi error aayega to terminal aur browser mein details dikhengi.
    socketio.run(app, debug=True)