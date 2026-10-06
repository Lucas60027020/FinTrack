from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_connection, init_db
from datetime import date
import csv
import io

app = Flask(
    __name__,
    template_folder="../frontend/templates",
    static_folder="../frontend/static"
)

app.config["SECRET_KEY"] = "fintrack-hackathon-secret"

init_db()


def login_required():
    return "user_id" in session


@app.route("/")
def index():
    return redirect(url_for("dashboard" if login_required() else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please fill in all fields.", "error")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must contain at least 6 characters.", "error")
            return render_template("register.html")

        db = get_connection()

        try:
            db.execute(
                """
                INSERT INTO users (name, email, password_hash)
                VALUES (?, ?, ?)
                """,
                (name, email, generate_password_hash(password))
            )
            db.commit()
        except Exception:
            db.close()
            flash("An account with that email already exists.", "error")
            return render_template("register.html")

        db.close()

        flash("Account created successfully.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_connection()

        user = db.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        db.close()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["role"] = user["role"]

            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    if not login_required():
        return redirect(url_for("login"))

    db = get_connection()
    user_id = session["user_id"]

    transactions = db.execute(
        """
        SELECT *
        FROM transactions
        WHERE user_id = ?
        ORDER BY transaction_date DESC, id DESC
        """,
        (user_id,)
    ).fetchall()

    totals = db.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expenses
        FROM transactions
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    category_rows = db.execute(
        """
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE user_id = ? AND type = 'expense'
        GROUP BY category
        ORDER BY total DESC
        LIMIT 6
        """,
        (user_id,)
    ).fetchall()

    budgets = db.execute(
        """
        SELECT *
        FROM budgets
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,)
    ).fetchall()

    db.close()

    income = float(totals["income"])
    expenses = float(totals["expenses"])
    balance = income - expenses

    if expenses == 0:
        savings_rate = 100 if income else 0
    else:
        savings_rate = round((balance / income) * 100, 1) if income else 0

    if expenses > income and income > 0:
        insight = "Your expenses are currently higher than your income. Consider reviewing your largest spending categories."
    elif income > 0 and savings_rate >= 20:
        insight = "Good work. Your current savings rate is healthy. Keep tracking your spending consistently."
    elif income > 0:
        insight = "You are building your financial history. Try setting category budgets to improve your savings rate."
    else:
        insight = "Add your first income and expense to start getting personalized financial insights."

    return render_template(
        "dashboard.html",
        user_name=session["user_name"],
        income=income,
        expenses=expenses,
        balance=balance,
        savings_rate=savings_rate,
        transactions=transactions,
        category_rows=category_rows,
        budgets=budgets,
        insight=insight
    )


@app.route("/transactions/add", methods=["POST"])
def add_transaction():
    if not login_required():
        return redirect(url_for("login"))

    transaction_type = request.form.get("type")
    amount = request.form.get("amount")
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    transaction_date = request.form.get("transaction_date") or str(date.today())

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        flash("Enter a valid amount.", "error")
        return redirect(url_for("dashboard"))

    if transaction_type not in ("income", "expense") or not category:
        flash("Please provide valid transaction details.", "error")
        return redirect(url_for("dashboard"))

    db = get_connection()

    db.execute(
        """
        INSERT INTO transactions
        (user_id, type, amount, category, description, transaction_date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session["user_id"],
            transaction_type,
            amount,
            category,
            description,
            transaction_date
        )
    )

    db.commit()
    db.close()

    flash("Transaction added successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/transactions/delete/<int:transaction_id>", methods=["POST"])
def delete_transaction(transaction_id):
    if not login_required():
        return redirect(url_for("login"))

    db = get_connection()

    db.execute(
        """
        DELETE FROM transactions
        WHERE id = ? AND user_id = ?
        """,
        (transaction_id, session["user_id"])
    )

    db.commit()
    db.close()

    flash("Transaction deleted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/budgets/add", methods=["POST"])
def add_budget():
    if not login_required():
        return redirect(url_for("login"))

    category = request.form.get("category", "").strip()
    amount = request.form.get("amount")
    month = request.form.get("month")

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        flash("Enter a valid budget amount.", "error")
        return redirect(url_for("dashboard"))

    if not category or not month:
        flash("Enter a category and month.", "error")
        return redirect(url_for("dashboard"))

    db = get_connection()

    try:
        db.execute(
            """
            INSERT INTO budgets (user_id, category, amount, month)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, category, month)
            DO UPDATE SET amount = excluded.amount
            """,
            (session["user_id"], category, amount, month)
        )
        db.commit()
        flash("Budget saved.", "success")
    except Exception:
        flash("Could not save the budget.", "error")

    db.close()

    return redirect(url_for("dashboard"))


@app.route("/export")
def export_csv():
    if not login_required():
        return redirect(url_for("login"))

    db = get_connection()

    rows = db.execute(
        """
        SELECT transaction_date, type, category, description, amount
        FROM transactions
        WHERE user_id = ?
        ORDER BY transaction_date DESC
        """,
        (session["user_id"],)
    ).fetchall()

    db.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        ["Date", "Type", "Category", "Description", "Amount"]
    )

    for row in rows:
        writer.writerow([
            row["transaction_date"],
            row["type"],
            row["category"],
            row["description"] or "",
            row["amount"]
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=fintrack_transactions.csv"
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
