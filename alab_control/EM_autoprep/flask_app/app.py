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
tem_grid_holder_opened = "000"
tem_grid_holder_closed = "150"


# Define 3D printer constants for safe operation - be careful when changing or the machine might break
MEASURED_BASE_HEIGHT = 71 #71 is the measured value that David measured
MAX_EXPOSURE_DISTANCE = 25.0
SPEED_VLOW = 0.005
SPEED_LOW = 0.02
SPEED_NORMAL = 0.5
PAUSE = 2
PAUSE_VAC = 11

# Define variables for physical control of the 3D printer
global_robot = None
connection_failures = 0
FAILURE_THRESHOLD = 2
POWER_CYCLE_WAIT = 15  # seconds
STANDBY_WAIT = 9  # seconds

# CSV files management variables
CWD = os.getcwd()
positions = {}
rootpath = os.path.join(CWD, "alab_control","EM_autoprep","Positions") #'\EM_autoprep\Positions\'
#rootpath = os.path.join(CWD, "EM_autoprep","Positions") #'\EM_autoprep\Positions\'
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

    def disconnect(self):
        if hasattr(self, 'serial') and self.serial is not None:
            try:
                self.serial.close()
            except Exception as e:
                print(f"Error closing serial port: {e}")

# Define action functions
def send_plc_command(message):
    print('Sending to PLC >> ' + message)  # Print to Python terminal
    socketio.emit('function_response', {'result': 'Sending to PLC >> ' + message})
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((plc_ip, plc_port))
            s.sendall(message.encode())
            data = s.recv(1024)
            decoded = data.decode('utf-8')
            
            # Debug logging
            print(f'Raw received data (length: {len(data)}): {data}')
            print(f'Decoded data (length: {len(decoded)}): {decoded}')
            
            if 'MACSTAT' in decoded:
                # Try to receive additional data
                try:
                    additional_data = s.recv(1024)
                    if additional_data:
                        decoded += additional_data.decode('utf-8')
                        print(f'Additional data received: {additional_data.decode("utf-8")}')
                except socket.timeout:
                    print("No additional data after MACSTAT")

            print('Socket reply>>' + decoded)
            socketio.emit('function_response', {'result': decoded})
            return decoded
            
    except socket.timeout:
        error_message = "No response from the server (timeout)."
        print(error_message)
        socketio.emit('function_response', {'result': error_message})
        return error_message
    except socket.error as e:
        error_message = f"Socket error: {e}"
        print(error_message)
        socketio.emit('function_response', {'result': error_message})
        return error_message


def button_action(button_id):
    print(f"Button action called for {button_id}")
    return f"Button action performed for {button_id}"

def test_robot_connection():
    global global_robot, connection_failures
    
    if global_robot is None:
        return False
        
    try:
        # Try to get printer status or send a simple M115 command
        global_robot.printer.write("M115\n".encode())  # Get Firmware Info
        response = global_robot.printer.readline().decode()
        if "ok" in response.lower() or "FIRMWARE_NAME" in response:
            connection_failures = 0  # Reset counter on successful connection
            return True
        else:
            raise Exception("Invalid response from printer")
    except Exception as e:
        print(f"Robot connection test failed: {e}")
        connection_failures += 1
        return False

def reset_robot_connection():
    global global_robot, connection_failures
    
    try:
        # Clean up existing connection
        if global_robot is not None:
            try:
                global_robot.disconnect()
            except:
                pass
            global_robot = None
            
        # Physical reset sequence
        print("Initiating robot reset sequence...")
        socketio.emit('function_response', {'result': "Initiating robot reset sequence..."})
        
        control_panel_shutdown()
        print("Waiting for power cycle...")
        socketio.emit('function_response', {'result': "Waiting for power cycle..."})
        time.sleep(POWER_CYCLE_WAIT)
        
        control_panel_standby()
        print("Waiting for standby...")
        socketio.emit('function_response', {'result': "Waiting for standby..."})
        time.sleep(STANDBY_WAIT)
        
        # Try to establish new connection using existing test function
        success, result = c3dp_test_connectivity(complete_test=True)
        if success:
            connection_failures = 0
            print("Robot reset successful")
            socketio.emit('function_response', {'result': "Robot reset successful"})
            return True
                
        print("Robot reset failed")
        socketio.emit('function_response', {'result': result})  # Use the detailed result from test
        return False
        
    except Exception as e:
        print(f"Error during robot reset: {e}")
        socketio.emit('function_response', {'result': f"Error during robot reset: {e}"})
        return False
    
def handle_robot_operation(operation_func, *args, **kwargs):
    global global_robot, connection_failures
    
    # Initialize robot if needed
    if global_robot is None:
        success, connectivity_result = c3dp_test_connectivity(complete_test=False)
        if not success:
            print(connectivity_result)
            socketio.emit('function_response', {'result': connectivity_result})
            return False
    
    # Test existing connection
    try:
        if not global_robot.test_connection():
            connection_failures += 1
            if connection_failures >= FAILURE_THRESHOLD:
                print(f"Connection failed {connection_failures} times. Attempting reset...")
                socketio.emit('function_response', 
                    {'result': f"Connection failed {connection_failures} times. Attempting reset..."})
                success, result = c3dp_test_connectivity(complete_test=False)
                if not success:
                    return False
            else:
                print(f"Connection failed {connection_failures} times")
                socketio.emit('function_response', 
                    {'result': f"Connection failed {connection_failures} times"})
                return False
        
        # Reset failure counter on successful connection
        connection_failures = 0
        
        # Execute the requested operation
        return operation_func(*args, **kwargs)
        
    except Exception as e:
        print(f"Error during operation: {str(e)}")
        socketio.emit('function_response', {'result': f"Error during operation: {str(e)}"})
        return False

def c3dp_test_connectivity(complete_test=False):
    try:
        global global_robot
        
        # If we already have a connection, test it
        if global_robot is not None:
            if global_robot.test_connection():
                return True, "3D Printer connection already established and working."
            else:
                # Clean up failed connection
                try:
                    global_robot.disconnect()
                except:
                    pass
                global_robot = None

        # Try to establish new connection
        try:
            robot = SamplePrepEnder3(c3dp_com_port)
            if robot.test_connection():
                global_robot = robot
                return True, "3D Printer connection established successfully."
            else:
                robot.disconnect()
                raise Exception("Connected but failed communication test")
                
        except Exception as e:
            raise Exception(f"Failed to establish connection: {e}")

    except Exception as var_error:
        if complete_test:
            result = f"An error occurred: {var_error}. \n\nAvailable ports:\n"
            ports = list(serial.tools.list_ports.comports())
            for p in ports:
                result += f"{p}\n"
        else:
            result = f"Could not connect to printer: {var_error}"
        return False, result
    
def handle_control_panel_operation(operation_func, *args, **kwargs):
    # Check control panel status
    status = control_panel_get_macstat()
    
    if "SHUTDWN" in status:
        print("Control panel is shutdown. Attempting to start...")
        socketio.emit('function_response', {'result': "Control panel is shutdown. Attempting to start..."})
        
        # Try to start the panel
        control_panel_standby()
        time.sleep(STANDBY_WAIT)
        
        # Check status again
        status = control_panel_get_macstat()
        if "STANDBY" not in status:
            error_msg = "Failed to start control panel"
            print(error_msg)
            socketio.emit('function_response', {'result': error_msg})
            return False
            
    if "STANDBY" not in status:
        error_msg = f"Control panel is not ready. Current status: {status}"
        print(error_msg)
        socketio.emit('function_response', {'result': error_msg})
        return False
        
    # If we get here, the panel is ready, execute the operation
    try:
        return operation_func(*args, **kwargs)
    except Exception as e:
        error_msg = f"Operation failed: {e}"
        print(error_msg)
        socketio.emit('function_response', {'result': error_msg})
        return False

def ping(ping_ip):
    try:
        # Different parameters for Windows vs Unix-based systems
        if os.name == 'nt':  # Windows
            command = ["ping", "-n", "2", ping_ip]
        else:  # Mac/Linux
            command = ["ping", "-c", "2", ping_ip]
        
        # Run the ping command
        result = subprocess.run(command, capture_output=True, text=True)
        
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

def control_panel_get_macstat():
    return send_plc_command("MACSTAT")

def control_panel_shutdown():
    return send_plc_command("SHUTDWN")

def control_panel_sem_stage_open():
    def _stage_open_operation():
        return send_plc_command(f"SEMSTORG{sem_stage_opened}")
    
    return handle_control_panel_operation(_stage_open_operation)

def control_panel_sem_stage_close():
    def _stage_close_operation():
        return send_plc_command(f"SEMSTORG{sem_stage_closed}")
    
    return handle_control_panel_operation(_stage_close_operation)

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
            send_plc_command("TEMPREPVAC1") #temporarily set to TEM pump due to the SEM one being broken right now
        else:
            send_plc_command("TEMSTORVAC0") #SEMSTORVAC0
    if destination == "TEM":
        if status:
            send_plc_command("TEMPREPVAC1")
        else:
            send_plc_command("TEMSTORVAC0")

def device_step_zero():
    def _zero_operation(robot):
        try:
            robot.gohome()
            control_panel_standby()
            print("Device zeroed successfully")
            socketio.emit('function_response', {'result': "Device zeroed successfully"})
            return True
        except Exception as var_error:
            print(f"An error occurred during zeroing: {var_error}")
            socketio.emit('function_response', {'result': f"An error occurred during zeroing: {var_error}"})
            return False

    return handle_robot_operation(_zero_operation)

def device_step_final(robot=None):
    def _final_operation(robot):
        try:
            # Set machine to standby
            control_panel_standby()
            
            # Extending the bed always homes Z
            if device_extend_bed():
                print("Sample preparation completed successfully.")
                socketio.emit('function_response', {'result': "Sample preparation completed successfully."})
                return True
            else:
                raise Exception("Bed extension failed")
                
        except Exception as e:
            error_message = f"Error in final steps: {str(e)}"
            print(error_message)
            socketio.emit('function_response', {'result': error_message})
            return False

    # If a robot instance was passed, use it directly
    if robot is not None:
        return _final_operation(robot)
    else:
        # Otherwise use the global robot management system
        return handle_robot_operation(_final_operation)

def device_extend_bed():
    def _extend_operation(robot):
        try:
            print("3DP bed extension requested.")
            socketio.emit('function_response', {'result': "3DP bed extension requested."})
            
            # Get current position
            robot.get_current_position()
            current_pos = robot.position
            print(f"Current position: {current_pos}")
            
            # Check if Z position is safe
            if current_pos[2] > 15:
                print("Z position unsafe. Moving to safe position first...")
                robot.speed = SPEED_NORMAL
                robot.moveto(*robot.intermediate_pos["PRE_EXTEND_POS"])
            else:
                # Z is safe, just ensure X is at safe position
                print("Z position safe. Moving X to safe position...")
                robot.speed = SPEED_NORMAL
                robot.moveto(15, current_pos[1], current_pos[2])
            
            # Now extend the bed
            print("Moving to BED_EXTENDED position...")
            robot.speed = SPEED_NORMAL
            robot.moveto(*robot.intermediate_pos["BED_EXTENDED"])
            
            print("3DP bed extended successfully")
            socketio.emit('function_response', {'result': "3DP bed extended."})
            return True
                
        except Exception as e:
            error_message = f"3DP bed couldn't be extended: {e}"
            print(error_message)
            socketio.emit('function_response', {'result': error_message})
            return False
            
    return handle_robot_operation(
        _extend_operation,
        robot=global_robot
    )

def send_manual_plc_command(command):
    try:
        response = send_plc_command(command)
        
        # Check if the response contains error messages
        if "error" in response.lower() or "timeout" in response.lower():
            print(f"PLC command failed: {response}")
            socketio.emit('function_response', {'result': f"PLC command failed: {response}"})
            return False
        else:
            print(f"PLC command response: {response}")
            socketio.emit('function_response', {'result': response})
            return True
            
    except Exception as e:
        error_message = f"Error in send_manual_plc_command: {str(e)}"
        print(error_message)
        socketio.emit('function_response', {'result': error_message})
        return False

def move_robot_manual(x, y, z, c3dp_speed):
    result = "Requesting robot to move to: x=" + x + " y=" + y + " z=" + z + " at speed=" + c3dp_speed
    print(result)
    socketio.emit('function_response', {'result': result})
    def _move_operation(robot):
        try:
            # Convert string inputs to float
            x_pos = float(x)
            y_pos = float(y)
            z_pos = float(z)
            c3dp_speed_manual = float(c3dp_speed)
            
            # Move robot to specified position
            robot.speed = c3dp_speed_manual
            robot.moveto(x_pos, y_pos, z_pos)
            result = "Robot moved successfully to position"
            print(result)
            socketio.emit('function_response', {'result': result})
            return True
            
        except ValueError:
            error = "Error: Please enter valid numbers for coordinates"
            print(error)
            socketio.emit('function_response', {'result': error})
            return False
        except Exception as e:
            error = f"Error during movement: {str(e)}"
            print(error)
            socketio.emit('function_response', {'result': error})
            return False

    return handle_robot_operation(_move_operation)

def home_robot_manual():
    def _home_operation(robot):
        try:
            robot.gohome()
            result = "Robot successfully homed"
            print(result)
            socketio.emit('function_response', {'result': result})
            return True
        except Exception as e:
            error = f"Error during homing: {str(e)}"
            print(error)
            socketio.emit('function_response', {'result': error})
            return False

    return handle_robot_operation(_home_operation)

def sem_process_action(voltage, c_height, distance, etime, origin, destination):
    def _sem_operation(robot, voltage, c_height, distance, etime, origin, destination):
        try:
            # Format voltage and time to 5 characters with leading zeros
            voltage = f"{int(voltage):05d}"
            etime = f"{int(etime):05d}"

            print(f"SEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}")
            socketio.emit('function_response', {'result': f"SEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}"})

            try:
                robot.gohome()
                
            except Exception as var_error:
                print(f"An error occurred: {var_error}")
                socketio.emit('function_response', {'result': f"An error occurred: {var_error}"})
                return False

            robot.speed = SPEED_NORMAL
            robot.moveto(*robot.intermediate_pos["ZHOME"])
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

                robot.moveto(*robot.clean_stub_pos[origin])
                # Descending needle
                robot.moveto(*robot.clean_stub_pos["STRAY_Z1"])
                robot.speed = SPEED_LOW
                
                # Descending needle, slower speed
                robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                robot.speed = SPEED_VLOW
                
                # Trying to collect stub delicately
                robot.moveto(*robot.clean_stub_pos["STRAY_Z3"])
                robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                robot.speed = SPEED_NORMAL
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                print("Checking if stub was picked...")
                socketio.emit('function_response', {'result': "Checking if stub was picked..."})
                robot.moveto(*robot.equipment_pos["LASER_SEM"])
                robot.moveto(*robot.equipment_pos["LASER_SEM_Z1"])

                if control_panel_laser_status() == "LASER1":
                    print("Stub was picked!")
                    socketio.emit('function_response', {'result': "Stub was picked!"})
                    stub_picked = True
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    break
                else:
                    print("Stub was not detected. Trying again...")
                    socketio.emit('function_response', {'result': "Stub was not detected. Trying again..."})
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    stub_pick_trials = stub_pick_trials+1

            if stub_picked:
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                robot.moveto(*robot.intermediate_pos["CHARGER_SEM"])
                robot.moveto(z=MEASURED_BASE_HEIGHT - int(c_height))

                socketio.emit('function_response', {'result': f"Setting at: {MEASURED_BASE_HEIGHT - int(c_height)} mm."})
                robot.moveto(z=MEASURED_BASE_HEIGHT -  int(c_height) + int(distance))
                socketio.emit('function_response', {'result': f"Exposing at: {MEASURED_BASE_HEIGHT - int(c_height) + int(distance)} mm."})
                print(f"Stub will be exposed to {voltage} kV for {etime} ms.")
                socketio.emit('function_response', {'result': f"Stub will be exposed to {voltage} kV for {etime} ms."})
                control_panel_hvps_setting(voltage,etime)
                time.sleep(int(etime)/1000+2)
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                
                print(f"Delivering stub to {origin}.")
                socketio.emit('function_response', {'result': f"Delivering stub to {origin}."})
                robot.moveto(*robot.clean_stub_pos[origin])
                robot.moveto(*robot.clean_stub_pos["STRAY_Z1"])
                robot.speed = SPEED_LOW
                robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                robot.speed = SPEED_VLOW
                robot.moveto(*robot.clean_stub_pos["STRAY_Z3"])
                control_panel_vacuum("SEM",False)
                time.sleep(PAUSE_VAC)
                robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                robot.speed = SPEED_NORMAL
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                robot.moveto(*robot.intermediate_pos["HOME"])

            device_step_final(robot)
            return True

        except Exception as e:
            print(f"Error in process: {e}")
            socketio.emit('function_response', {'result': f"Error in process: {e}"})
            return False

    return handle_control_panel_operation(
        lambda: handle_robot_operation(
            _sem_operation, 
            robot=global_robot,
            voltage=voltage, 
            c_height=c_height, 
            distance=distance, 
            etime=etime, 
            origin=origin, 
            destination=destination
        )
    )

def tem_process_action(voltage, c_height, distance, etime, origin, destination):
    def _tem_operation(robot, voltage, c_height, distance, etime, origin, destination):
        try:
            # Format voltage and time to 5 characters with leading zeros
            voltage = f"{int(voltage):05d}"
            etime = f"{int(etime):05d}"

            print(f"TEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}")
            socketio.emit('function_response', {'result': f"TEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}"})

            try:
                robot.gohome()
                
            except Exception as var_error:
                print(f"An error occurred: {var_error}")
                socketio.emit('function_response', {'result': f"An error occurred: {var_error}"})
                return False

            robot.speed = SPEED_NORMAL
            robot.moveto(*robot.intermediate_pos["ZHOME"])
            print(f"Collecting grid from {origin}")
            socketio.emit('function_response', {'result': f"Collecting grid from {origin}."})

            grid_pick_trials = 0
            grid_picked = False

            # for now, sending direct commands. This must be changed to a function to standardise the code
            control_panel_tem_grid_holder_open()
            time.sleep(1.5)
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

                robot.moveto(*robot.clean_disk_pos[origin])
                # Descending needle
                robot.moveto(*robot.clean_disk_pos["TCTRAY_Z1"])
                robot.speed = SPEED_LOW
                
                # Descending needle, slower speed
                robot.moveto(*robot.clean_disk_pos["TCTRAY_Z2"])
                robot.speed = SPEED_VLOW
                
                # Trying to collect grid delicately
                robot.moveto(*robot.clean_disk_pos["TCTRAY_Z3"])
                robot.moveto(*robot.clean_disk_pos["TCTRAY_Z2"])
                robot.speed = SPEED_NORMAL
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                print("Checking if grid was picked...")
                socketio.emit('function_response', {'result': "Checking if grid was picked..."})
                robot.moveto(*robot.equipment_pos["LASER_TEM"])
                robot.moveto(*robot.equipment_pos["LASER_TEM_Z1"])

                if control_panel_laser_status() == "LASER1":
                    print("Grid was picked!")
                    socketio.emit('function_response', {'result': "Grid was picked!"})
                    grid_picked = True
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    break
                else:
                    print("Grid was not detected. Trying again...")
                    socketio.emit('function_response', {'result': "Grid was not detected. Trying again..."})
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    grid_pick_trials = grid_pick_trials + 1

            if grid_picked:
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                time.sleep(1)
                control_panel_tem_grid_holder_close()
                time.sleep(1)
                robot.moveto(*robot.intermediate_pos["CHARGER_TEM"])
                robot.moveto(z=MEASURED_BASE_HEIGHT - int(c_height))

                socketio.emit('function_response', {'result': f"Setting at: {MEASURED_BASE_HEIGHT - int(c_height)} mm."})
                robot.moveto(z=MEASURED_BASE_HEIGHT - int(c_height) + int(distance))
                socketio.emit('function_response', {'result': f"Exposing at: {MEASURED_BASE_HEIGHT - int(c_height) + int(distance)} mm."})
                print(f"Grid will be exposed to {voltage} kV for {etime} ms.")
                socketio.emit('function_response', {'result': f"Grid will be exposed to {voltage} kV for {etime} ms."})
                control_panel_hvps_setting(voltage,etime)
                time.sleep(int(etime)/1000+2)
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                
                print(f"Delivering grid to {destination}.")
                socketio.emit('function_response', {'result': f"Delivering grid to {destination}."})
                control_panel_tem_grid_holder_open()
                time.sleep(1)
                robot.moveto(*robot.used_disk_pos[destination])
                robot.moveto(*robot.used_disk_pos["TETRAY_Z1"])
                robot.speed = SPEED_LOW
                robot.moveto(*robot.used_disk_pos["TETRAY_Z2"])
                robot.speed = SPEED_VLOW
                robot.moveto(*robot.used_disk_pos["TETRAY_Z3"])
                control_panel_vacuum("TEM",False)
                time.sleep(PAUSE_VAC)
                robot.moveto(*robot.used_disk_pos["TETRAY_Z2"])
                robot.speed = SPEED_NORMAL
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                time.sleep(1)
                control_panel_tem_grid_holder_close()
                time.sleep(1)
                robot.moveto(*robot.intermediate_pos["HOME"])
                

            device_step_final(robot)

            return True

        except Exception as e:
            print(f"Error in process: {e}")
            socketio.emit('function_response', {'result': f"Error in process: {e}"})
            return False

    return handle_control_panel_operation(
        lambda: handle_robot_operation(
            _tem_operation, 
            robot=global_robot,
            voltage=voltage, 
            c_height=c_height, 
            distance=distance, 
            etime=etime, 
            origin=origin, 
            destination=destination
        )
    )


# Map function names to handlers
function_map = {
    'button': button_action,
    'sem_process': sem_process_action,
    'tem_process': tem_process_action,
    'c3dp_test_connectivity': c3dp_test_connectivity,
    'c3dp_test_connectivity_machine_test_page': c3dp_test_connectivity_machine_test_page,
    'server_test_connectivity': server_test_connectivity,
    'control_panel_standby': control_panel_standby,
    'control_panel_shutdown': control_panel_shutdown,
    'control_panel_sem_stage_open': control_panel_sem_stage_open,
    'control_panel_sem_stage_close': control_panel_sem_stage_close,
    'control_panel_tem_grid_holder_open': control_panel_tem_grid_holder_open,
    'control_panel_tem_grid_holder_close': control_panel_tem_grid_holder_close,
    'device_extend_bed': device_extend_bed,
    'robot_manual_move': move_robot_manual,
    'robot_manual_home': home_robot_manual,
    'send_manual_plc_command': send_manual_plc_command
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
    print("Received data:", data)  # debug line
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
        elif function_type == 'tem_process':
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
        elif function_type == 'robot_manual_move':
            return action_function(
                x=data.get('x'),
                y=data.get('y'),
                z=data.get('z'),
                c3dp_speed = data.get('c3dp_speed')
            )
        elif function_type == 'robot_manual_home':
            return action_function()
        elif function_type == 'send_manual_plc_command':
                return action_function(
                    command=data.get('command')
                )
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