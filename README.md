# Split Bills

Split Bills is a Flask web application for managing shared trip expenses. Users can create trips, add members, record expenses, view balances, and calculate settlements between participants.

## Features

- User registration and login
- Create and manage trips
- Add expenses for trip members
- View expense history
- Calculate settlements
- Dashboard and analytics pages

## Technologies Used

- Python
- Flask
- MySQL
- HTML
- CSS

## Project Structure

```text
splitbills/
├── app.py
├── models.py
├── config.py
├── settlemet.py
├── database.sql
├── requirements.txt
├── templates/
├── style/
└── README.md
```

## How to Run

1. Clone the repository:

```bash
git clone https://github.com/purnimathapa/splitbills.git
```

2. Go into the project folder:

```bash
cd splitbills
```

3. Install required packages:

```bash
pip install -r requirements.txt
```

4. Set up the database using `database.sql`.

5. Run the Flask app:

```bash
python app.py
```

6. Open the app in your browser:

```text
http://127.0.0.1:5000
```

## Author

Purnima Thapa

