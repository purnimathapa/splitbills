"""Register all HTTP route modules on the Flask app."""


def register_routes(app, bcrypt):
    from routes.analytics import register as register_analytics
    from routes.auth import register as register_auth
    from routes.dashboard import register as register_dashboard
    from routes.expenses import register as register_expenses
    from routes.groups import register as register_groups
    from routes.payments import register as register_payments

    register_auth(app, bcrypt)
    register_dashboard(app)
    register_expenses(app)
    register_groups(app)
    register_payments(app)
    register_analytics(app)
