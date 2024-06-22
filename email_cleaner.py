from flask import Flask, request, render_template, redirect, url_for, session
import imaplib
import email
import logging
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
app.secret_key = 'hello'

logging.basicConfig(level=logging.DEBUG)

keywords = ["sale", "promotion", "discount", "offer", "last chance"]

def get_mail_connection(email_account, password, provider):
    try:
        if provider == 'gmail':
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
        elif provider == 'outlook':
            mail = imaplib.IMAP4_SSL('imap-mail.outlook.com')
        else:
            raise ValueError("Unsupported email provider")

        mail.login(email_account, password)
        return mail, None
    except Exception as e:
        logging.error(f"Failed to connect to email server: {str(e)}")
        return None, str(e)

def move_to_trash(mail, mail_id, provider):
    try:
        if provider == 'gmail':
            result = mail.store(mail_id, '+X-GM-LABELS', '\\Trash')
        else:
            result = mail.move(mail_id, 'Deleted Items')

        if result[0] == 'OK':
            logging.debug(f"Moved email ID {mail_id} to trash")
            return True
        else:
            logging.error(f"Failed to move email ID {mail_id} to trash")
            return False
    except Exception as e:
        logging.error(f"Error moving email ID {mail_id} to trash: {str(e)}")
        return False

def process_batch(mail_ids, email_account, password, provider):
    mail, error = get_mail_connection(email_account, password, provider)
    if error:
        return error, 0

    deleted_count = 0
    try:
        mail.select("INBOX")
        logging.debug(f"Processing batch with {len(mail_ids)} emails: {mail_ids}")
        for mail_id in mail_ids:
            try:
                status, data = mail.fetch(mail_id, '(RFC822)')
                if status != 'OK':
                    continue

                msg = email.message_from_bytes(data[0][1])
                subject = msg['subject']
                body = ""

                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True)
                            if isinstance(body, bytes):
                                body = body.decode(errors='ignore')
                            break
                else:
                    body = msg.get_payload(decode=True)
                    if isinstance(body, bytes):
                        body = body.decode(errors='ignore')

                if any(keyword in (subject or "").lower() for keyword in keywords) or any(keyword in (body or "").lower() for keyword in keywords):
                    if move_to_trash(mail, mail_id, provider):
                        deleted_count += 1
            except Exception as e:
                logging.error(f"Error processing email ID {mail_id}: {str(e)}")
                continue

        return None, deleted_count
    except Exception as e:
        logging.error(f"Error processing emails: {str(e)}")
        return str(e), deleted_count
    finally:
        mail.logout()

def search_emails(mail, keywords):
    try:
        email_ids_set = set()  # Use a set to avoid duplicates
        for keyword in keywords:
            # Search for each keyword in both body and subject
            status, messages_body = mail.search(None, f'BODY "{keyword}"')
            status, messages_subject = mail.search(None, f'SUBJECT "{keyword}"')
            
            if status == 'OK':
                email_ids_set.update(messages_body[0].split())
                email_ids_set.update(messages_subject[0].split())
            else:
                logging.error(f"Failed to search for keyword: {keyword}")
        
        email_ids = list(email_ids_set)
        logging.debug(f"Search returned {len(email_ids)} emails for keywords: {keywords}")
        return email_ids
    except Exception as e:
        logging.error(f"Error searching emails: {str(e)}")
        return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    email_account = request.form.get('email')
    password = request.form.get('password')
    provider = request.form.get('provider')
    
    if not email_account or not password or not provider:
        return render_template('index.html', message="All fields are required.")
    
    session['email_account'] = email_account
    session['password'] = password
    session['provider'] = provider
    return redirect(url_for('delete_promotional_emails'))

@app.route('/delete_promotional_emails')
def delete_promotional_emails():
    email_account = session.get('email_account')
    password = session.get('password')
    provider = session.get('provider')
    if not email_account or not password or not provider:
        return redirect(url_for('index'))

    batch_size = 100  # Adjust batch size as needed

    mail, error = get_mail_connection(email_account, password, provider)
    if error:
        logging.error(error)
        return render_template('index.html', message=error)

    try:
        logging.debug("Selecting INBOX folder")
        status, response = mail.select("INBOX")
        if status != 'OK':
            raise Exception("Failed to connect to the mailbox")

        logging.debug("Fetching emails")
        email_ids = search_emails(mail, keywords)
        total_emails = len(email_ids)
        logging.debug(f"Total emails found: {total_emails}")

        if total_emails > batch_size:
            email_ids = email_ids[:batch_size]

        batches = [email_ids[i:i + batch_size] for i in range(0, len(email_ids), batch_size)]
        logging.debug(f"Batches created: {len(batches)}")

        deleted_count = 0
        with ThreadPoolExecutor(max_workers=4) as executor:  # Adjust number of workers as needed
            results = list(executor.map(lambda batch: process_batch(batch, email_account, password, provider), batches))

        errors = [result[0] for result in results if result[0]]
        deleted_count += sum(result[1] for result in results)

        if errors:
            logging.error("Errors occurred during batch processing")
            return render_template('delete.html', message="Some errors occurred during processing", deleted_count=deleted_count, show_button=True)

        logging.debug(f"Promotional emails deleted successfully: {deleted_count} emails")

    except Exception as e:
        logging.error(f"Error processing emails: {str(e)}")
        return render_template('delete.html', message=str(e), deleted_count=0)
    finally:
        mail.logout()

    return render_template('delete.html', message="First batch of promotional emails deleted successfully", deleted_count=deleted_count, show_button=True)

@app.route('/process_all')
def process_all():
    email_account = session.get('email_account')
    password = session.get('password')
    provider = session.get('provider')
    if not email_account or not password or not provider:
        return redirect(url_for('index'))

    mail, error = get_mail_connection(email_account, password, provider)
    if error:
        logging.error(error)
        return render_template('delete.html', message=error, deleted_count=0)

    try:
        logging.debug("Selecting INBOX folder")
        status, response = mail.select("INBOX")
        if status != 'OK':
            raise Exception("Failed to connect to the mailbox")

        logging.debug("Fetching all emails")
        email_ids = search_emails(mail, keywords)
        total_emails = len(email_ids)
        logging.debug(f"Total emails found: {total_emails}")

        batches = [email_ids[i:i + 100] for i in range(0, total_emails, 100)]
        logging.debug(f"Batches created: {len(batches)}")

        deleted_count = 0
        with ThreadPoolExecutor(max_workers=4) as executor:  # Adjust number of workers as needed
            results = list(executor.map(lambda batch: process_batch(batch, email_account, password, provider), batches))

        errors = [result[0] for result in results if result[0]]
        deleted_count += sum(result[1] for result in results)

        if errors:
            logging.error("Errors occurred during batch processing")
            return render_template('delete.html', message="Some errors occurred during processing", deleted_count=deleted_count)

        logging.debug(f"All promotional emails deleted successfully: {deleted_count} emails")

    except Exception as e:
        logging.error(f"Error processing emails: {str(e)}")
        return render_template('delete.html', message=str(e), deleted_count=deleted_count)
    finally:
        mail.logout()

    return render_template('delete.html', message="All promotional emails deleted successfully", deleted_count=deleted_count)

if __name__ == '__main__':
    app.run(debug=True)
