from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_connection, init_db
from datetime import date
import csv
import io
import calendar

app = Flask(
    __name__,
    template_folder="../frontend/templates",
    static_folder="../frontend/static"
)

app.config["SECRET_KEY"] = "fintrack-hackathon-secret"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

init_db()


def logged_in():
    return "user_id" in session


def require_login():
    if not logged_in():
        return redirect(url_for("login"))
    return None


@app.route("/")
def index():
    return redirect(url_for("dashboard" if logged_in() else "login"))


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
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
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
    guard = require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    db = get_connection()

    totals = db.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0) income,
            COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0) expenses
        FROM transactions
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    recent = db.execute("""
        SELECT *
        FROM transactions
        WHERE user_id = ?
        ORDER BY transaction_date DESC, id DESC
        LIMIT 8
    """, (user_id,)).fetchall()

    categories = db.execute("""
        SELECT category, SUM(amount) total
        FROM transactions
        WHERE user_id = ? AND type='expense'
        GROUP BY category
        ORDER BY total DESC
        LIMIT 6
    """, (user_id,)).fetchall()

    monthly = db.execute("""
        SELECT
            substr(transaction_date,1,7) month,
            COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END),0) income,
            COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END),0) expenses
        FROM transactions
        WHERE user_id=?
        GROUP BY substr(transaction_date,1,7)
        ORDER BY month DESC
        LIMIT 6
    """, (user_id,)).fetchall()

    db.close()

    income = float(totals["income"])
    expenses = float(totals["expenses"])
    balance = income - expenses
    savings = round((balance / income) * 100, 1) if income else 0

    if balance < 0:
        insight = "Your spending is currently higher than your income. Review your largest expense categories."
    elif savings >= 30:
        insight = "Excellent progress. You are currently saving more than 30% of recorded income."
    elif savings >= 20:
        insight = "Good progress. Your savings rate is healthy. Keep tracking consistently."
    elif income:
        insight = "You have started building your financial picture. Setting budgets can help increase savings."
    else:
        insight = "Add your income and expenses to unlock personalized financial insights."

    return render_template(
        "dashboard.html",
        income=income,
        expenses=expenses,
        balance=balance,
        savings=savings,
        recent=recent,
        categories=categories,
        monthly=monthly,
        insight=insight
    )


@app.route("/transactions")
def transactions():
    guard = require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    search = request.args.get("search", "").strip()
    kind = request.args.get("type", "")
    category = request.args.get("category", "")

    db = get_connection()

    query = """
        SELECT *
        FROM transactions
        WHERE user_id = ?
    """
    params = [user_id]

    if search:
        query += " AND (category LIKE ? OR description LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    if kind in ("income", "expense"):
        query += " AND type = ?"
        params.append(kind)

    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY transaction_date DESC, id DESC"

    rows = db.execute(query, params).fetchall()

    categories = db.execute("""
        SELECT DISTINCT category
        FROM transactions
        WHERE user_id=?
        ORDER BY category
    """, (user_id,)).fetchall()

    db.close()

    return render_template(
        "transactions.html",
        transactions=rows,
        categories=categories,
        search=search,
        selected_type=kind,
        selected_category=category,
        today=date.today().isoformat()
    )


@app.route("/transactions/add", methods=["POST"])
def add_transaction():
    guard = require_login()
    if guard:
        return guard

    kind = request.form.get("type")
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    transaction_date = request.form.get("transaction_date") or date.today().isoformat()

    try:
        amount = float(request.form.get("amount", "0"))
    except ValueError:
        amount = 0

    if kind not in ("income", "expense") or amount <= 0 or not category:
        flash("Please enter a valid amount, type and category.", "error")
        return redirect(url_for("transactions"))

    db = get_connection()
    db.execute("""
        INSERT INTO transactions
        (user_id,type,amount,category,description,transaction_date)
        VALUES (?,?,?,?,?,?)
    """, (
        session["user_id"],
        kind,
        amount,
        category,
        description,
        transaction_date
    ))
    db.commit()
    db.close()

    flash("Transaction added successfully.", "success")
    return redirect(url_for("transactions"))


@app.route("/transactions/delete/<int:transaction_id>", methods=["POST"])
def delete_transaction(transaction_id):
    guard = require_login()
    if guard:
        return guard

    db = get_connection()
    db.execute("""
        DELETE FROM transactions
        WHERE id=? AND user_id=?
    """, (transaction_id, session["user_id"]))
    db.commit()
    db.close()

    flash("Transaction deleted.", "success")
    return redirect(url_for("transactions"))


@app.route("/budgets", methods=["GET", "POST"])
def budgets():
    guard = require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    db = get_connection()

    if request.method == "POST":
        category = request.form.get("category", "").strip()
        month = request.form.get("month", "")
        try:
            amount = float(request.form.get("amount", "0"))
        except ValueError:
            amount = 0

        if not category or not month or amount <= 0:
            db.close()
            flash("Enter a valid category, amount and month.", "error")
            return redirect(url_for("budgets"))

        db.execute("""
            INSERT INTO budgets(user_id,category,amount,month)
            VALUES(?,?,?,?)
            ON CONFLICT(user_id,category,month)
            DO UPDATE SET amount=excluded.amount
        """, (user_id, category, amount, month))
        db.commit()

        flash("Budget saved successfully.", "success")

    budget_rows = db.execute("""
        SELECT *
        FROM budgets
        WHERE user_id=?
        ORDER BY month DESC, category
    """, (user_id,)).fetchall()

    result = []

    for budget in budget_rows:
        spent = db.execute("""
            SELECT COALESCE(SUM(amount),0)
            FROM transactions
            WHERE user_id=?
              AND type='expense'
              AND category=?
              AND substr(transaction_date,1,7)=?
        """, (
            user_id,
            budget["category"],
            budget["month"]
        )).fetchone()[0]

        percentage = round((spent / budget["amount"]) * 100, 1)
        percentage = min(percentage, 100)

        result.append({
            "category": budget["category"],
            "month": budget["month"],
            "amount": budget["amount"],
            "spent": spent,
            "percentage": percentage,
            "remaining": max(budget["amount"] - spent, 0)
        })

    categories = db.execute("""
        SELECT DISTINCT category
        FROM transactions
        WHERE user_id=?
        ORDER BY category
    """, (user_id,)).fetchall()

    db.close()

    return render_template(
        "budgets.html",
        budgets=result,
        categories=categories,
        current_month=date.today().strftime("%Y-%m")
    )


@app.route("/assistant", methods=["GET", "POST"])
def assistant():
    guard = require_login()
    if guard:
        return guard

    answer = None
    question = ""

    if request.method == "POST":
        question = request.form.get("question", "").strip()
        q = question.lower()
        user_id = session["user_id"]

        db = get_connection()

        totals = db.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END),0) income,
                COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END),0) expenses
            FROM transactions
            WHERE user_id=?
        """, (user_id,)).fetchone()

        top = db.execute("""
            SELECT category, SUM(amount) total
            FROM transactions
            WHERE user_id=? AND type='expense'
            GROUP BY category
            ORDER BY total DESC
            LIMIT 1
        """, (user_id,)).fetchone()

        db.close()

        income = float(totals["income"])
        expenses = float(totals["expenses"])
        balance = income - expenses

        if any(word in q for word in ["balance", "left", "remaining"]):
            answer = f"Your current recorded balance is ₹{balance:,.2f}."

        elif any(word in q for word in ["expense", "spent", "spending"]):
            if top:
                answer = (
                    f"You have recorded ₹{expenses:,.2f} in expenses. "
                    f"Your largest spending category is {top['category']} "
                    f"at ₹{top['total']:,.2f}."
                )
            else:
                answer = "You haven't recorded any expenses yet."

        elif any(word in q for word in ["income", "earned", "salary"]):
            answer = f"Your total recorded income is ₹{income:,.2f}."

        elif any(word in q for word in ["save", "saving"]):
            if income:
                rate = (balance / income) * 100
                answer = (
                    f"Your current savings rate is {rate:.1f}%. "
                    "Try setting category budgets and reviewing your largest expense."
                )
            else:
                answer = "Add some income first and I can calculate your savings rate."

        elif any(word in q for word in ["category", "biggest", "largest"]):
            if top:
                answer = (
                    f"Your biggest expense category is {top['category']} "
                    f"with ₹{top['total']:,.2f} spent."
                )
            else:
                answer = "You don't have enough expense data yet."

        else:
            answer = (
                "I can analyze your FinTrack data. Try asking: "
                "\"What is my balance?\", \"How much did I spend?\", "
                "\"What is my biggest expense?\", or \"How can I save more?\""
            )

    return render_template(
        "assistant.html",
        answer=answer,
        question=question
    )


@app.route("/export")
def export_csv():
    guard = require_login()
    if guard:
        return guard

    db = get_connection()

    rows = db.execute("""
        SELECT transaction_date,type,category,description,amount
        FROM transactions
        WHERE user_id=?
        ORDER BY transaction_date DESC
    """, (session["user_id"],)).fetchall()

    db.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Date", "Type", "Category", "Description", "Amount"])

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
            "Content-Disposition":
            "attachment; filename=fintrack_transactions.csv"
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
