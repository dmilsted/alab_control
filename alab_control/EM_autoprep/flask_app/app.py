from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import socket
import threading
import serial.tools.list_ports  # Ensure this is imported
from alab_control.ender3 import Ender3
import subprocess
import csv
import os
import time

app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading')  # Important: Specify async_mode

# Define IPs and ports, and 3DP COM port
host_ip = "localhost"  # Set to listen on all interfaces
web_port = 8000
udp_port = 8001
server_ip = "142.251.214.142" #change to server's IP. This is google :)
plc_ip = '192.168.0.46'
plc_port = 8888
c3dp_com_port = "COM7"

# Define numeric values for linear actuators
sem_stage_opened = "015"
sem_stage_closed = "180"
tem_grid_holder_opened = "040"
tem_grid_holder_closed = "180"


# Defined 3D printer constants for safe operation - be careful when changing or the machine might break
MEASURED_BASE_HEIGHT = 71 #71 is the measured value that David measured
MAX_EXPOSURE_DISTANCE = 25.0
SPEED_VLOW = 0.005
SPEED_LOW = 0.02
SPEED_NORMAL = 0.05 #0.5
PAUSE = 2
PAUSE_VAC = 11

# CSV files management variables
CWD = os.getcwd()
positions = {}
rootpath = os.path.join(CWD, "alab_control","EM_autoprep","Positions") #'\EM_autoprep\Positions\'
clean_disks_filename = 'disks_tray_clean.csv'
used_disks_filename = 'disks_tray_used.csv'
equipment_filename = 'equipment.csv'
intermediate_positions_filename = 'intermediate_positions.csv'
phenom_holder_positions_filename = 'phenom_stubs.csv'
phenom_handler_filename = 'phenom_handler.csv'
stubs_tray_filename = 'stubs_tray.csv'

def float_or_none(s):
  if s == 'None':
    return None
  return float(s)

def read_CSV_into_positions(path): 
  with open(path, mode ='r') as file:
    csvFile = csv.reader(file)
    for lines in csvFile:
      #Each line has a list of 4 arguments, argument 0 is the name of the position, and argument 1, 2, 3 correspond to x, y, z, respectively
      positions[lines[0]] = ((float_or_none(lines[1]), float_or_none(lines[2]), float_or_none(lines[3])))
  return positions

class SamplePrepEnder3(Ender3):
    # positions
    clean_disk_pos = read_CSV_into_positions(
        path=os.path.join(rootpath, clean_disks_filename)
    )
    used_disk_pos = read_CSV_into_positions(
        path=os.path.join(rootpath, used_disks_filename)
    )
    equipment_pos = read_CSV_into_positions(
        path=os.path.join(rootpath, equipment_filename)
    )
    intermediate_pos = read_CSV_into_positions(
        path=os.path.join(rootpath, intermediate_positions_filename)
    )
    used_stub_pos = read_CSV_into_positions(
        path=os.path.join(rootpath, phenom_holder_positions_filename)
    )
    phenom_handler_pos = read_CSV_into_positions(
        path=os.path.join(rootpath, phenom_handler_filename)
    )
    clean_stub_pos = read_CSV_into_positions(
        path=os.path.join(rootpath, stubs_tray_filename)
    )

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

def c3dp_test_connectivity(complete_test=False):
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
        print(result)
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

def control_panel_laser_status():
    return send_plc_command("SEMPREPTEST")

def control_panel_hvps_setting(v,t):
    return send_plc_command(f"EXPOSURV{v}T{t}")

def control_panel_vacuum(destination,status=False):
    if destination == "SEM":
        if status:
            send_plc_command("SEMPREPVAC1")
        else:
            send_plc_command("STANDBY")
    if destination == "TEM":
        if status:
            send_plc_command("TEMPREPVAC1")
        else:
            send_plc_command("STANDBY")

def device_step_zero():
    success, connectivity_result = c3dp_test_connectivity(complete_test=False)
    if success:
        r = SamplePrepEnder3(c3dp_com_port)
        try:
            r.gohome()
        except Exception as var_error:
            print(f"An error occurred: {var_error}")
            socketio.emit("An error occurred: {var_error}")  # Assuming this emits the error message to the client
            return False  # Return False to indicate failure
    else:
        # Connectivity test failed, return the error message
        socketio.emit(connectivity_result)
        return connectivity_result

    control_panel_standby()  # This will only run if no exceptions occurred
    return True  # Return True to indicate success

def device_step_final():
    r = SamplePrepEnder3(c3dp_com_port)
    r.moveto(*r.intermediate_pos["ZHOME"])
    r.gohome()
    control_panel_shutdown()
    return

def device_extend_bed():
    print("3DP bed extension requested.")
    socketio.emit('function_response', {'result': "3DP bed extension requested."})
    r = SamplePrepEnder3(c3dp_com_port)
    if device_step_zero():
        r.speed = SPEED_NORMAL
        r.moveto(*r.intermediate_pos["BED_EXTENDED"])
        print("3DP bed extended")
        socketio.emit('function_response', {'result': "3DP bed extended."})
    else:
        print("3DP bed couldn't be extended.")
        socketio.emit('function_response', {'result': "3DP bed couldn't be extended."})
    return

def sem_process_action(voltage, c_height, distance, etime, origin, destination):

    # TODO: The code should test each received variable. e.g., if distance is not beyond safety, if origin and destination exists, etc.

    success, connectivity_result = c3dp_test_connectivity(complete_test=False)
    if not success:
        print(connectivity_result)
        socketio.emit('function_response', {'result': connectivity_result})
        return False

    r = SamplePrepEnder3(c3dp_com_port)

    print(f"SEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}")
    socketio.emit('function_response', {'result': f"SEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}"})

    try:
        r.gohome()
        control_panel_standby()
    except Exception as var_error:
        print(f"An error occurred: {var_error}")
        socketio.emit('function_response', {'result': f"An error occurred: {var_error}"})
        return False
    r.speed = SPEED_NORMAL
    r.moveto(*r.intermediate_pos["ZHOME"])
    print(f"Collecting stub from {origin}")
    socketio.emit('function_response', {'result': f"Collecting stub from {origin}."})

    stub_pick_trials = 0
    stub_picked = False
    control_panel_vacuum("SEM",True)
    while True:
        if stub_pick_trials > 2:
            print("Stub not picked 3 times in a row. Aborted.")
            socketio.emit('function_response', {'result': "Stub not picked 3 times in a row. Aborted."})
            control_panel_vacuum("SEM",False)
            break
        else:
            print("Trying to pick the stub...")
            socketio.emit('function_response', {'result': "Trying to pick the stub..."})

        r.moveto(*r.clean_stub_pos[origin])
        # Descending needle
        r.moveto(*r.clean_stub_pos["STRAY_Z1"])
        r.speed = SPEED_LOW
        
        # Descending needle, slower speed
        r.moveto(*r.clean_stub_pos["STRAY_Z2"])
        r.speed = SPEED_VLOW
        
        # Trying to collect stub delicately
        r.moveto(*r.clean_stub_pos["STRAY_Z3"])
        r.moveto(*r.clean_stub_pos["STRAY_Z2"])
        r.speed = SPEED_NORMAL
        r.moveto(*r.intermediate_pos["ZHOME"])
        print("Checking if stub was picked...")
        socketio.emit('function_response', {'result': "Checking if stub was picked..."})
        r.moveto(*r.equipment_pos["LASERSEM"])
        r.moveto(*r.equipment_pos["LASERSEM_Z1"])

        if control_panel_laser_status() == "LASER1":
        #if True: #for debugging, delete!
            print("Stub was picked!")
            socketio.emit('function_response', {'result': "Stub was picked!"})
            stub_picked = True
            r.moveto(*r.intermediate_pos["ZHOME"])
            break
        else:
            print("Stub was not detected. Trying again...")
            socketio.emit('function_response', {'result': ".Stub was not detected. Trying again..."})
            r.moveto(*r.intermediate_pos["ZHOME"])
            stub_pick_trials = stub_pick_trials+1

    if stub_picked:
        r.moveto(*r.intermediate_pos["ZHOME"])
        r.moveto(*r.intermediate_pos["CHARGER"])
        r.moveto(z=MEASURED_BASE_HEIGHT - int(c_height))

        #TODO: check if the exposition heights make sense. It seems it is reversed (going up when it should go down, and vice -versa)
        socketio.emit('function_response', {'result': f"Setting at: {MEASURED_BASE_HEIGHT - int(c_height)} mm."})
        r.moveto(z=MEASURED_BASE_HEIGHT -  int(c_height) + int(distance))
        socketio.emit('function_response', {'result': f"Exposing at: {MEASURED_BASE_HEIGHT - int(c_height) + int(distance)} mm."})
        print(f"Stub will be exposed to {voltage} kV for {etime} ms.")
        socketio.emit('function_response', {'result': f"Stub will be exposed to {voltage} kV for {etime} ms."})
        control_panel_hvps_setting(voltage,etime)
        time.sleep(int(etime)/1000+2)
        r.moveto(*r.intermediate_pos["ZHOME"])
        
        print(f"Delivering stub to {origin}.")
        socketio.emit('function_response', {'result': f"Delivering stub to {origin}."})
        r.moveto(*r.clean_stub_pos[origin])
        r.moveto(*r.clean_stub_pos["STRAY_Z1"])
        r.speed = SPEED_LOW
        r.moveto(*r.clean_stub_pos["STRAY_Z2"])
        r.speed = SPEED_VLOW
        r.moveto(*r.clean_stub_pos["STRAY_Z3"])
        control_panel_vacuum("SEM",False)
        time.sleep(PAUSE_VAC)
        r.moveto(*r.clean_stub_pos["STRAY_Z2"])
        r.speed = SPEED_NORMAL
        r.moveto(*r.intermediate_pos["ZHOME"])

    device_step_final()

    return

def tem_process_action(voltage, c_height, distance, etime, origin, destination):
    print(f"TEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}")
    socketio.emit('function_response', {'result': f"TEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}"})
    r = SamplePrepEnder3(c3dp_com_port)

    # TODO: The code should test each received variable. e.g., if distance is not beyond safety, if origin and destination exists, etc.

    if device_step_zero():
        r.speed = SPEED_NORMAL
        r.moveto(*r.intermediate_pos["ZHOME"])
        print(f"Collecting grid from {origin}")
        socketio.emit('function_response', {'result': f"Collecting grid from {origin}."})

        grid_pick_trials = 0
        grid_picked = False
        control_panel_vacuum("TEM",True)
        while True:
            if grid_pick_trials > 2:
                print("Grid not picked 3 times in a row. Aborted.")
                socketio.emit('function_response', {'result': "Grid not picked 3 times in a row. Aborted."})
                control_panel_vacuum("TEM",False)
                break
            else:
                print("Trying to pick the grid...")
                socketio.emit('function_response', {'result': "Trying to pick the grid..."})

            r.moveto(*r.clean_stub_pos[origin])
            # Descending needle
            r.moveto(*r.clean_stub_pos["STRAY_Z1"])
            r.speed = SPEED_LOW
            
            # Descending needle, slower speed
            r.moveto(*r.clean_stub_pos["STRAY_Z2"])
            r.speed = SPEED_VLOW
            
            # Trying to collect stub delicately
            r.moveto(*r.clean_stub_pos["STRAY_Z3"])
            r.moveto(*r.clean_stub_pos["STRAY_Z2"])
            r.speed = SPEED_NORMAL
            r.moveto(*r.intermediate_pos["ZHOME"])
            print("Checking if grid was picked...")
            socketio.emit('function_response', {'result': "Checking if grid was picked..."})
            r.moveto(*r.equipment_pos["LASERTEM"])
            r.moveto(*r.equipment_pos["LASERTEM_Z1"])

            if control_panel_laser_status() == "LASER1":
            #if True: #for debugging, delete!
                print("Grid was picked!")
                socketio.emit('function_response', {'result': "Grid was picked!"})
                stub_picked = True
                r.moveto(*r.intermediate_pos["ZHOME"])
                break
            else:
                print("Grid was not detected. Trying again...")
                socketio.emit('function_response', {'result': ".Grid was not detected. Trying again..."})
                r.moveto(*r.intermediate_pos["ZHOME"])
                grid_pick_trials = grid_pick_trials+1

        if stub_picked:
            r.moveto(*r.intermediate_pos["ZHOME"])
            r.moveto(*r.intermediate_pos["CHARGER"])
            r.moveto(z=MEASURED_BASE_HEIGHT - int(c_height))

            #TODO: check if the exposition heights make sense. It seems it is reversed (going up when it should go down, and vice -versa)
            socketio.emit('function_response', {'result': f"Setting at: {MEASURED_BASE_HEIGHT - int(c_height)} mm."})
            r.moveto(z=MEASURED_BASE_HEIGHT -  int(c_height) + int(distance))
            socketio.emit('function_response', {'result': f"Exposing at: {MEASURED_BASE_HEIGHT - int(c_height) + int(distance)} mm."})
            print(f"Grid will be exposed to {voltage} kV for {etime} ms.")
            socketio.emit('function_response', {'result': f"Grid will be exposed to {voltage} kV for {etime} ms."})
            control_panel_hvps_setting(voltage,etime)
            time.sleep(int(etime)/1000+2)
            r.moveto(*r.intermediate_pos["ZHOME"])
            
            print(f"Delivering grid to {origin}.")
            socketio.emit('function_response', {'result': f"Delivering grid to {origin}."})
            r.moveto(*r.clean_stub_pos[origin])
            r.moveto(*r.clean_stub_pos["STRAY_Z1"])
            r.speed = SPEED_LOW
            r.moveto(*r.clean_stub_pos["STRAY_Z2"])
            r.speed = SPEED_VLOW
            r.moveto(*r.clean_stub_pos["STRAY_Z3"])
            control_panel_vacuum("TEM",False)
            time.sleep(PAUSE_VAC)
            r.moveto(*r.clean_stub_pos["STRAY_Z2"])
            r.speed = SPEED_NORMAL
            r.moveto(*r.intermediate_pos["ZHOME"])

        device_step_final()

    return


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
    'control_panel_tem_grid_holder_close': control_panel_tem_grid_holder_close,
    'device_extend_bed': device_extend_bed
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
    elif page == 'tem-tray':
        return render_template('pages/tem_tray.html')
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

@app.route('/tem_tray')
def tem_page():
    return render_template('tem_tray.html')

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
                etime=data.get('time'),
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
        elif function_type == 'device_extend_bed':
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