from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import socket
import threading
import serial.tools.list_ports  # Ensure this is imported
from alab_control.ender3 import Ender3
import subprocess
import csv
import os

app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading')  # Important: Specify async_mode

# Define IPs and ports, and 3DP COM port
host_ip = "localhost"  # Set to listen on all interfaces
web_port = 8000
udp_port = 8001
server_ip = "142.251.214.142" #change to server's IP. This is google :)
plc_ip = '192.168.0.46'
plc_port = 8888
c3dp_com_port = "COM6"

# Define numeric values for linear actuators
sem_stage_opened = "015"
sem_stage_closed = "180"
tem_grid_holder_opened = "040"
tem_grid_holder_closed = "180"


# Defined 3D printer constants for safe operation - be careful when changing or the machine might break
MEASURED_BASE_HEIGHT = 71 #71 is the measured value that David measured
MAX_EXPOSURE_DISTANCE = 25.0
CRUCIBLE_HEIGHT = 39
SPEED_VLOW = 0.005
SPEED_LOW = 0.02
SPEED_NORMAL = 0.5
PAUSE = 2
PAUSE_VAC = 11

# Define action functions
def send_plc_command(message):
    print('Sending to PLC >> ' + message)  # Print to Python terminal
    socketio.emit('function_response', {'result': 'Sending to PLC >> ' + message})  # Emit to webpage terminal
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)  # Set a timeout for the connection
            s.connect((plc_ip, plc_port))
            s.sendall(message.encode())
            data = s.recv(1024)
            decoded = data.decode('utf-8')
            print('Socket reply>>' + decoded)  # Print to Python terminal
            socketio.emit('function_response', {'result': decoded})  # Emit to webpage terminal
            return decoded
    except socket.timeout:
        error_message = "No response from the server (timeout)."
        print(error_message)  # Print to Python terminal
        socketio.emit('function_response', {'result': error_message})  # Emit to webpage terminal
        return error_message
    except socket.error as e:
        error_message = f"Socket error: {e}"
        print(error_message)  # Print to Python terminal
        socketio.emit('function_response', {'result': error_message})  # Emit to webpage terminal
        return error_message


def button_action(button_id):
    print(f"Button action called for {button_id}")
    return f"Button action performed for {button_id}"

def c3dp_test_connectivity(complete_test=True):
    class SamplePrepEnder3(Ender3):
        pass

    try:
        r = SamplePrepEnder3(c3dp_com_port)
        result = "3D Printer connection established and working well."
        return True, result
    except Exception as var_error:
        if complete_test:
            result = f"An error occurred: {var_error}. \n\nThese are the available connections:\n"
            ports = list(serial.tools.list_ports.comports())
            for p in ports:
                result += f"{p}\n\n"
        else:
            result = "It was not possible to connect to the printer. Test the connectivity on the machine test page."
        return False, result

def ping(ping_ip):
    try:
        # Run the ping command
        result = subprocess.run(["ping", "-c", "2", ping_ip], capture_output=True, text=True)
        
        # Check the result
        if result.returncode == 0:
            ping_result = result.stdout
            return f"Ping called to {ping_ip}. It was successful:\n{ping_result}"
        else:
            return f"Ping function was called, but IP {ping_ip} did not respond:\n{result.stderr}"
    except Exception as e:
        return f"An error occurred while pinging {ping_ip}: {str(e)}"

def server_test_connectivity():
    #ping(server_ip)
    return ping(server_ip)

def c3dp_test_connectivity_machine_test_page():
    return c3dp_test_connectivity(True)

def control_panel_standby():
    return send_plc_command("STANDBY")

def control_panel_shutdown():
    return send_plc_command("SHUTDWN")

def control_panel_sem_stage_open():
    return send_plc_command(f"SEMSTORG{sem_stage_opened}")

def control_panel_sem_stage_close():
    return send_plc_command(f"SEMSTORG{sem_stage_closed}")

def control_panel_tem_grid_holder_open():
    return send_plc_command(f"TEMPREPL{tem_grid_holder_opened}")

def control_panel_tem_grid_holder_close():
    return send_plc_command(f"TEMPREPL{tem_grid_holder_closed}")

def sem_process_action(voltage, c_height, distance, time, origin, destination):
    print(f"SEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={time}, origin={origin}, destination={destination}")
    socketio.emit('function_response', {'result': f"SEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={time}, origin={origin}, destination={destination}"})
    # Test connectivity with a simple message
    success, connectivity_result = c3dp_test_connectivity(complete_test=False)
    
    # Check if the connectivity test passed
    if success:
        # Connectivity test passed, perform the home command
        class SamplePrepEnder3(Ender3):
            pass
        
        r = SamplePrepEnder3(c3dp_com_port)
        r.gohome()

    else:
        # Connectivity test failed, return the error message
        return connectivity_result
    return 
    #f"SEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={time}, origin={origin}, destination={destination}"

# Map function names to handlers
function_map = {
    'button': button_action,
    'sem_process': sem_process_action,
    'c3dp_test_connectivity': c3dp_test_connectivity,
    'c3dp_test_connectivity_machine_test_page': c3dp_test_connectivity_machine_test_page,
    'server_test_connectivity': server_test_connectivity,
    'control_panel_standby': control_panel_standby,
    'control_panel_shutdown': control_panel_shutdown,
    'control_panel_sem_stage_open': control_panel_sem_stage_open,
    'control_panel_sem_stage_close': control_panel_sem_stage_close,
    'control_panel_tem_grid_holder_open': control_panel_tem_grid_holder_open,
    'control_panel_tem_grid_holder_close': control_panel_tem_grid_holder_close
}

@app.route('/get_page/<page>')
def get_page(page):
    if page == 'home':
        return render_template('pages/home.html')
    elif page == 'machine-test':
        return render_template('pages/machine_test.html')
    elif page == 'sem-tray':
        return render_template('pages/sem_tray.html')
    elif page == 'sem-stage':
        return render_template('pages/sem_stage.html')
    elif page == 'tem':
        return render_template('pages/tem.html')
    else:
        return f"Page not found: {page}", 404

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/sem_tray')
def sem_tray_page():
    return render_template('sem_tray.html')

@app.route('/sem_stage')
def sem_stage_page():
    return render_template('sem_stage.html')

@app.route('/tem')
def tem_page():
    return render_template('tem.html')

@socketio.on('call_function')
def handle_socket_function(data):
    function_name = data.get('function')
    result = dispatch_action({'function': function_name, 'id': function_name})
    socketio.emit('function_response', {'result': result})

@app.route('/handle_function', methods=['POST'])
def handle_function():
    data = request.json
    print("Received HTTP request:", data)  # Debug log
    result = dispatch_action(data)
    
    '''
    # Only emit via Socket.IO for non-button actions
    if data.get('function') != 'button':
        socketio.emit('function_response', {'result': result})
    '''
    
    return jsonify({"status": "success", "message": result})
    

def dispatch_action(data):
    function_type = data.get('function')
    identifier = data.get('id')
    action_function = function_map.get(function_type)
    
    if action_function is None:
        return f"Unknown function type: {function_type}"
    
    try:
        if function_type == 'button':
            return action_function(identifier)
        elif function_type == 'sem_process':
            return action_function(
                voltage=data.get('voltage'),
                c_height=data.get('c_height'),
                distance=data.get('distance'),
                time=data.get('time'),
                origin=data.get('origin'),
                destination=data.get('destination')
            )
        elif function_type == 'c3dp_test_connectivity':
            return action_function()
        elif function_type == 'c3dp_test_connectivity_machine_test_page':
            return action_function()
        elif function_type == 'server_test_connectivity':
            return action_function() 
        elif function_type == 'control_panel_standby':
            return action_function() 
        elif function_type == 'control_panel_shutdown':
            return action_function()
        elif function_type == 'control_panel_sem_stage_open':
            return action_function()
        elif function_type == 'control_panel_sem_stage_close':
            return action_function()
        elif function_type == 'control_panel_tem_grid_holder_open':
            return action_function()
        elif function_type == 'control_panel_tem_grid_holder_close':
            return action_function()
        else:
            return "Function type not supported"
    except Exception as e:
        return f"Error: {str(e)}"

# UDP server function to handle commands and respond via UDP and Socket.IO
def udp_server():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.bind((host_ip, udp_port))
        print(f"UDP server listening on {host_ip}:{udp_port}")

        while True:
            try:
                data, addr = udp_socket.recvfrom(1024)
                message = data.decode('utf-8')
                print(f"Received UDP command: {message} from {addr}")

                # Process the command and get result
                params = parse_udp_message(message)
                result = dispatch_action(params)
                print("Result:", result)

                # Emit to all connected clients using socketio.emit
                socketio.emit('function_response', {'result': result}, namespace='/')
                
                # Send response back through UDP and include an EOF marker
                response = result + "\n"
                udp_socket.sendto(response.encode('utf-8'), addr)

            except Exception as e:
                error_msg = f"Error in UDP server: {str(e)}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg}, namespace='/')

# Helper function to parse UDP messages in key=value format
def parse_udp_message(message):
    try:
        return dict(item.split("=") for item in message.split("&"))
    except ValueError:
        return {}

# Start the UDP server in a separate thread
if __name__ == '__main__':
    # Initialize Socket.IO with engineio_logger for debugging
    socketio = SocketIO(app, logger=True, engineio_logger=True)
    
    # Start the UDP server thread
    udp_thread = threading.Thread(target=udp_server, daemon=True)
    udp_thread.start()
    print(f"UDP server thread started. Listening on {host_ip}:{udp_port}")
    
    # Run the Flask application
    socketio.run(app, host=host_ip, port=web_port, debug=True, allow_unsafe_werkzeug=True)