# Calendra

Intelligent inventory demand planning for a college prototype. A retailer uploads daily sales, keeps a business calendar (festivals, promotions, paydays, public holidays), and gets two forecasts:

- a plain weekday forecast
- a calendar-aware forecast that multiplies a normal day by the lift learned from past events

The overview turns that forecast into a purchase suggestion: how many units to order, by which date, and why.

## Run it locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open http://127.0.0.1:8000/

Demo login: `planner`  
Password: `CalendraDemo2026`

On Windows, activate the environment with `.venv\Scripts\activate`.

## What to show

1. Sign in. The overview compares event-day error for the plain model and the calendar model.
2. The next-30-days chart marks the pre-Diwali sale and Diwali week. The calendar line rises into the festival and the plain line does not.
3. Open **Diwali gift hamper** or **LED string lights**. The page explains the order quantity, the supplier lead time, and the learned festival lift.
4. Change units on hand and update the suggestion.
5. Calendar lists the business dates. Add one event, then rebuild the forecast.
6. Sales data can reload the demo or accept a CSV / Excel file. A template is in `sample_data/sales_template.csv`.

## Working together

Clone the repository, create a branch for your change, and open a pull request. Avoid committing straight to `main` once both of you are pushing.

```bash
git checkout -b sejal/dashboard-copy
git push -u origin sejal/dashboard-copy
```
