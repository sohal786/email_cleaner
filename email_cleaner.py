from flask import Flask, request, render_template, redirect, url_for, session
import imaplib
import email
import logging
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
app.secret_key = 'hello'

logging.basicConfig(level=logging.DEBUG)

def get_mail_connection(email_account, password):
    try:
        mail = imaplib.IMAP4_SSL('imap-mail.outlook.com')
        mail.login(email_account, password)
        return mail, None
    except Exception as e:
        logging.error(f"Failed to connect to email server: {str(e)}")
        return None, str(e)

def process_batch(mail_ids, email_account, password):
    mail, error = get_mail_connection(email_account, password)
    if error:
        return error

    try:
        mail.select("INBOX")
        for mail_id in mail_ids:
            status, data = mail.fetch(mail_id, '(RFC822)')
            if status != 'OK':
                continue

            msg = email.message_from_bytes(data[0][1])
            subject = msg['subject']
            body = msg.get_payload(decode=True)

            if any(keyword in (subject or "").lower() for keyword in keywords) or any(keyword in (body or "").decode(errors='ignore').lower() for keyword in keywords):
                mail.store(mail_id, '+FLAGS', '\\Deleted')
                logging.debug(f"Marked email ID {mail_id} for deletion")

        mail.expunge()
        return None
    except Exception as e:
        logging.error(f"Error processing emails: {str(e)}")
        return str(e)
    finally:
        mail.logout()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    email_account = request.form['email']
    password = request.form['password']
    session['email_account'] = email_account
    session['password'] = password
    return redirect(url_for('delete_promotional_emails'))

@app.route('/delete_promotional_emails')
def delete_promotional_emails():
    email_account = session.get('email_account')
    password = session.get('password')
    if not email_account or not password:
        return redirect(url_for('index'))

    batch_size = 100  # Adjust batch size as needed

    mail, error = get_mail_connection(email_account, password)
    if error:
        logging.error(error)
        return render_template('index.html', message=error)

    try:
        logging.debug("Selecting INBOX folder")
        status, response = mail.select("INBOX")
        if status != 'OK':
            raise Exception("Failed to connect to the mailbox")

        logging.debug("Fetching emails")
        keywords = ["sale", "promotion", "discount", "offer", "last chance"]
        search_criteria = f'OR (BODY "{keywords[0]}") ' + ' '.join([f'(BODY "{keyword}")' for keyword in keywords[1:]])
        search_criteria = f'OR (SUBJECT "{keywords[0]}") ' + ' '.join([f'(SUBJECT "{keyword}")' for keyword in keywords[1:]])

        status, messages = mail.search(None, search_criteria)
        if status != "OK":
            raise Exception("Failed to fetch emails")

        email_ids = messages[0].split()
        total_emails = len(email_ids)

        batches = [email_ids[i:i + batch_size] for i in range(0, total_emails, batch_size)]

        with ThreadPoolExecutor(max_workers=4) as executor:  # Adjust number of workers as needed
            results = list(executor.map(lambda batch: process_batch(batch, email_account, password), batches))

        errors = [result for result in results if result]
        if errors:
            logging.error("Errors occurred during batch processing")
            return render_template('index.html', message="Some errors occurred during processing")

        logging.debug("Promotional emails deleted successfully")

    except Exception as e:
        logging.error(f"Error processing emails: {str(e)}")
        return render_template('index.html', message=str(e))
    finally:
        mail.logout()

    return render_template('index.html', message="Promotional emails deleted successfully")

if __name__ == '__main__':
    app.run(debug=True)
