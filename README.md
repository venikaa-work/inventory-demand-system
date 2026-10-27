# Inventory demand system

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

On a Mac, XGBoost needs OpenMP: `brew install libomp`.

Demo login: `planner`  
Password: `CalendraDemo2026`

On Windows, activate the environment with `.venv\Scripts\activate`.

## What to show

1. Sign in. The first page answers one question: how many units will the shop sell in the next 30 days?
2. Compare the number with holidays to the number without holidays. Diwali week is marked on the chart.
3. The scores underneath are from a hidden last month (MAE, RMSE, MAPE).
4. Upload accepts a whole-shop file (`date`, `total_sales`) or a file that also names each item.
5. What's next lists what is deliberately left for the final submission.

