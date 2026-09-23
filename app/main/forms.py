from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, DecimalField, DateField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, InputRequired, Length, Optional, NumberRange, Regexp
from config import Config


class AccountForm(FlaskForm):
    name = StringField("Account Name", validators=[DataRequired(), Length(max=80)])
    account_type = SelectField("Type", choices=[(t, t) for t in Config.ACCOUNT_TYPES])
    last4 = StringField("Last 4 digits (optional)", validators=[Optional(), Regexp(r"^\d{4}$", message="Enter exactly 4 digits.")])
    opening_balance = DecimalField("Opening Balance", validators=[InputRequired(), NumberRange(min=0)], default=0)
    submit = SubmitField("Save Account")


class AddMoneyForm(FlaskForm):
    # Optional so Cash accounts can omit it (amount is derived from note counts).
    # Non-cash accounts still enforce a positive amount in the route.
    amount = DecimalField("Amount", validators=[Optional(), NumberRange(min=0.01)])
    account_id = SelectField("Receiving Account", coerce=int, validators=[DataRequired()])
    date = DateField("Date", validators=[DataRequired()])
    note = StringField("Note / Source (optional)", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Add Money")


class ExpenseForm(FlaskForm):
    amount = DecimalField("Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    category_id = SelectField("Category", coerce=int, validators=[DataRequired()])
    subcategory_id = SelectField("Purpose", coerce=int, validators=[Optional()])
    payment_method = SelectField("Payment Method", choices=[(m, m) for m in Config.PAYMENT_METHODS])
    account_id = SelectField("Source Account", coerce=int, validators=[DataRequired()])
    date = DateField("Date", validators=[DataRequired()])
    note = StringField("Note (optional)", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Add Expense")


class BudgetForm(FlaskForm):
    amount = DecimalField("Monthly Budget", validators=[InputRequired(), NumberRange(min=0)])
    submit = SubmitField("Save Budget")


class CategoryForm(FlaskForm):
    name = StringField("Category Name", validators=[DataRequired(), Length(max=80)])
    submit = SubmitField("Add Category")


class SubcategoryForm(FlaskForm):
    name = StringField("Purpose Name", validators=[DataRequired(), Length(max=80)])
    submit = SubmitField("Add Purpose")


class RestoreForm(FlaskForm):
    password = StringField("Backup Password", validators=[DataRequired()])
    submit = SubmitField("Restore")
