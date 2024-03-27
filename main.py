import csv
import io
import os
import numpy as np
import pandas as pd
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
import logging
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

app = Flask(__name__)
# Set the secret key
app.secret_key = b'_5#y2L"F4Q8z\n\xec]/'


# Define the directory to store uploaded files
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


df = None
def extract_sheet_id(sheet_url):
   print('sheet_url', sheet_url)
   # Extract the Sheet ID from the provided URL
   start_index = sheet_url.find('/d/') + 3
   end_index = sheet_url.find('/', start_index)
   print('sheet_id', sheet_url[start_index:end_index])
   return sheet_url[start_index:end_index]


def process_google_sheet(sheet_url, range_name):
   try:
       # Extract the Sheet ID from the provided URL
       sheet_id = extract_sheet_id(sheet_url)
       service = get_google_sheets_service()
       # Use the Google Sheets API to get the spreadsheet
       spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
       # Use the Google Sheets API to read values from the specified Sheet
       result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
       values = result.get('values', [])
       # Extract the title of the first sheet (assuming there is only one sheet)
       title = spreadsheet['properties']['title']
       # Convert the data into a DataFrame
       global df
       df = pd.DataFrame(values[1:], columns=values[0])
       # Create the 'Key' column by combining 'ToName' and 'Tracking #' columns
       df['Key'] = df['ToName'].str[:3] + df['Tracking #'].str[-4:]


       return df, title, sheet_id, range_name
   except Exception as e:
       logging.error("Error processing Google Sheet: %s", e)
       raise ValueError(f"An error occurred while processing the Google Sheet: {str(e)}")



@app.route('/')
def index():
   return render_template('index.html')


@app.route('/process_sheet', methods=['POST'])
def process_sheet():
    sheet_url = request.form.get('sheet_url')
    range_name = request.form.get('range_name')


   # If not stored in session, use values from the form data
    if not sheet_url:
       sheet_url = session.get('sheet_url')
    if not range_name:
       range_name = session.get('range_name')


    # Validate the form fields
    if not sheet_url or not range_name:
        return jsonify({'error': 'sheet_url and range_name cannot be empty'})


    try:
        df, title, sheet_id, range_name = process_google_sheet(sheet_url, range_name)
        message = f"Currently working on {title}"
        # Store sheet_url and range_name in session
        session['sheet_url'] = sheet_url
        session['range_name'] = range_name
        session['sheet_id'] = sheet_id
        return render_template('index.html', sheetname=title, message=message, df=df.to_json(orient='records'))
    except ValueError as e:
        return jsonify({'error': str(e)})



@app.route('/upload', methods=['POST'])
def upload_file():
   global df
   if 'file' not in request.files:
       return 'No file part'
   file = request.files['file']
   if file.filename == '':
       return 'No selected file'
   if file:
       file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
       file.save(file_path)
       df = pd.read_excel(file_path, sheet_name='March')  # Load DataFrame

       # Extract first three characters of ToName and last three characters of Tracking #
       df['Key'] = df['ToName'].str[:3] + df['Tracking #'].str[-4:]

       # Write the updated DataFrame back to the Excel file
       df.to_excel(file_path, sheet_name='March', index=False)


       return render_template('index.html', filename=file.filename)




def find_matching_row(key):
   global df
   # Remove leading and trailing whitespace from the input string
   key = key.strip()
   # Check if the length of the string is greater than 26
   if len(key) > 26:
       # Keep the last 26 characters
       key = key[-26:]
   if df is not None:
       # Find the matching row based on the key or tracking number in the DataFrame
       matching_row = df[(df['Tracking #'] == key) | (df['Key'].str.upper() == key.upper())]  # Ignore case
       if not matching_row.empty:
           # Extract relevant information from the matching row
           tracking_number = matching_row['Tracking #'].iloc[0]
           item = matching_row['Product Description'].iloc[0]
           quantity = matching_row['QTY'].iloc[0]
           pack = matching_row['Items Send Packages'].iloc[0]
           name = matching_row['ToName'].iloc[0]
           address2 = matching_row['ToAddress2'].iloc[0]
           if pd.notnull(address2):
               address = (f"{matching_row['ToAddress1'].iloc[0]}, {address2},\n"
                          f"{matching_row['ToCity'].iloc[0]}, {matching_row['ToState'].iloc[0]} "
                          f"{matching_row['ToZip'].iloc[0]}\n")
           else:
               address = (f"{matching_row['ToAddress1'].iloc[0]},\n"
                          f"{matching_row['ToCity'].iloc[0]}, {matching_row['ToState'].iloc[0]} "
                          f"{matching_row['ToZip'].iloc[0]}\n")
           status = matching_row['STATUS'].iloc[0]
           # Construct the result string with identifiers
           result = (f"tracking_number:{tracking_number}|"
                     f"item:{item}|"
                     f"quantity:{quantity}|"
                     f"pack:{pack}|"
                     f"name:{name}|"
                     f"address:{address}|"
                     f"status:{status}")
           return result
       else:
           return "error:No matching row found."
   else:
       return "error:File path not found."


# Define the directory to store log files
LOG_FOLDER = 'logs'

# Ensure the logs directory exists
if not os.path.exists(LOG_FOLDER):
   os.makedirs(LOG_FOLDER)


# Define the path to Logs.csv
logs_file = os.path.join(LOG_FOLDER, 'Logs.csv')

# Check if Logs.csv exists
if os.path.exists(logs_file):
   # If the file exists, remove it
   os.remove(logs_file)


# Create initial DataFrame for logs
log_df = pd.DataFrame(columns=['tracking #', 'item description', 'quantity', 'timestamp', 'shift', 'status'])



def update_status(tracking_number, status):
   global df
   global log_df


   if df is not None:
       # Remove leading and trailing whitespace from the input string
       tracking_number = tracking_number.strip()


       # Update the value in df['STATUS']
       df.loc[df['Tracking #'] == tracking_number, 'STATUS'] = status


       # Check if the tracking number already exists in log_df
       if tracking_number in log_df['tracking #'].values:
           # Update the existing entry
           log_df.loc[log_df['tracking #'] == tracking_number, 'status'] = status
       else:
           # Create a new entry
           item_description = df.loc[df['Tracking #'] == tracking_number, 'Product Description'].values[0]
           quantity = df.loc[df['Tracking #'] == tracking_number, 'QTY'].values[0]
           timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
           shift = 'Current'


           new_log_entry = pd.DataFrame({
               'tracking #': [tracking_number],
               'item description': [item_description],
               'quantity': [quantity],
               'timestamp': [timestamp],
               'shift': [shift],
               'status': [status]
           })



           log_df = pd.concat([log_df, new_log_entry], ignore_index=True)


       # Save log_df to CSV file
       log_df.to_csv(os.path.join(LOG_FOLDER, 'Logs.csv'), index=False)


# Call update_status function with 'Shipped' status
def ship_last_item(tracking_number):
   update_status(tracking_number, 'Shipped')


# Call update_status function with 'Out of Stock' status
def item_oos(tracking_number):
   update_status(tracking_number, 'Out of Stock')



def get_google_sheets_service():
   credentials = service_account.Credentials.from_service_account_file('credentials.json')
   return build('sheets', 'v4', credentials=credentials)



@app.route('/process', methods=['POST'])
def process_input():
   data = request.json
   user_input = data.get('input')
   if user_input.lower() == 's':
       return ship_last_item_route()
   elif user_input.lower() == 'o':
       return item_oos_route()
   else:
       result = find_matching_row(user_input)
       return jsonify({'result': result})



@app.route('/ship_last_item', methods=['POST'])
def ship_last_item_route():
   data = request.json
   tracking_number = data.get('tracking_number')
   ship_last_item(tracking_number)
   return jsonify({'message': f"Status of Tracking#: {tracking_number} changed to Shipped"})


@app.route('/item_oos', methods=['POST'])
def item_oos_route():
   data = request.json
   tracking_number = data.get('tracking_number')
   item_oos(tracking_number)
   return jsonify({'message': f"Status of Tracking#: {tracking_number} changed to Out of Stock"})




def update_logs():
   # Load the credentials from the service account key file
   credentials = service_account.Credentials.from_service_account_file(
       'credentials.json',
       scopes=['https://www.googleapis.com/auth/drive']
   )


   # Create a service object for interacting with Google Drive
   drive_service = build('drive', 'v3', credentials=credentials)


   # Find the file ID of Logs.csv
   file_list = drive_service.files().list(q="name='Logs.csv'").execute()
   file_id = None
   for file in file_list.get('files', []):
       if file['name'] == 'Logs.csv':
           file_id = file['id']
           break


   # Download Logs.csv from Google Drive
   if file_id:
       request = drive_service.files().export_media(fileId=file_id, mimeType='text/csv')
       downloaded = io.BytesIO()
       downloader = MediaIoBaseDownload(downloaded, request)
       done = False
       while not done:
           status, done = downloader.next_chunk()


       # Read Logs.csv into DataFrame
       downloaded.seek(0)
       existing_log_df = pd.read_csv(downloaded)


       # Filter the DataFrame where the value in the 'shift' column is 'Current'
       current_shift_df = existing_log_df[existing_log_df['shift'] == 'Current']


       # Find the earliest timestamp from the filtered DataFrame
       shift_start = current_shift_df['timestamp'].min()


       # Get the current timestamp for shift end
       shift_end = datetime.now()


       # Create the 'shift' string
       shift = f"{shift_start} to {shift_end}"
       # Update the 'shift' column in the filtered DataFrame
       current_shift_df['shift'] = shift


       # Update the 'shift' column in the original log_df DataFrame
       existing_log_df.loc[current_shift_df.index, 'shift'] = current_shift_df['shift']


       # Save the updated DataFrame back to CSV
       with open('Logs.csv', 'w') as f:
           existing_log_df.to_csv(f, index=False)


       # Update Logs.csv on Google Drive
       media_body = MediaFileUpload('Logs.csv', mimetype='text/csv')
       updated_file = drive_service.files().update(fileId=file_id, media_body=media_body).execute()




# Function to update status in Google Sheets
def update_status_in_google_sheets(report_df, sheet_id, range_name):
   service = get_google_sheets_service()
   # Use the Google Sheets API to read values from the specified Sheet
   result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
   existing_values = result.get('values', [])
   if not existing_values:
       print('No data found in the Google Sheet.')
       return
   existing_df = pd.DataFrame(existing_values[1:], columns=existing_values[0])



   # Ensure column names are stripped of leading/trailing whitespaces and converted to lowercase
   existing_df.columns = existing_df.columns.str.strip().str.lower()
   report_df.columns = report_df.columns.str.strip().str.lower()



   # Iterate through rows to identify cells that need to be updated
   batch_updates = []
   for index, row in report_df.iterrows():
       # Find matching rows in the existing DataFrame
       matching_rows = existing_df.loc[existing_df['tracking #'] == row['tracking #']]
       if not matching_rows.empty:
           # Iterate through matching rows to identify cells for update
           for _, matching_row in matching_rows.iterrows():
               # Determine the cell to update for STATUS column
               status_column_index = existing_df.columns.get_loc('status')
               status_cell = f"{range_name}!{chr(65 + status_column_index)}{matching_row.name + 2}"  # Calculate column letter dynamically
               # Prepare batch update request for Status
               batch_updates.append({
                   'range': status_cell,
                   'values': [[row['status']]]
               })


   if batch_updates:
       update_logs()
       # Execute batch update request
       body = {'valueInputOption': 'RAW', 'data': batch_updates}
       request = service.spreadsheets().values().batchUpdate(spreadsheetId=sheet_id, body=body)
       response = request.execute()
   else:
       print('No updates needed.')




@app.route('/save-changes', methods=['POST'])
def save_changes():
   sheet_id = session.get('sheet_id')
   range_name = session.get('range_name')
   # Call the update_status_in_google_sheets function
   update_status_in_google_sheets(log_df, sheet_id, range_name)
   # Return a redirection response (status code 302)
   return redirect(request.url)



@app.route('/items-list')
def get_items_list():
   # Perform operations on the DataFrame to get the items list
   # For example, counting occurrences of each unique product description
   # For example, counting occurrences of each unique product description
   product_description_counts = df[(df['STATUS'] != 'Shipped') & (df['STATUS'] != 'Delivered')]
   # Strip whitespace and exclude empty strings
   product_description_counts.loc[:, 'Product Description'] = product_description_counts['Product Description'].str.strip()
   product_description_counts = product_description_counts[product_description_counts['Product Description'] != '']
   product_description_counts = product_description_counts['Product Description'].value_counts().reset_index()
   product_description_counts.columns = ['product_description', 'count']


   # Convert the DataFrame to a list of dictionaries
   items_list = product_description_counts.to_dict(orient='records')
   print('product_description_counts\n', product_description_counts)
   # Send the items list to JavaScript
   return jsonify(items_list)



if __name__ == '__main__':
   app.run(debug=True)

