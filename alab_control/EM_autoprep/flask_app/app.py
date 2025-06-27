from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import socket
import threading
import serial.tools.list_ports
from alab_control.ender3 import Ender3
import subprocess
import csv
import os
import time
from datetime import datetime, timedelta
import json
from database import (
    init_database, 
    start_process_run, 
    end_process_run, 
    log_error, 
    log_standalone_error,
    get_recent_operations_with_details,
    get_success_rate_by_process_type,
    get_most_common_errors,
    get_process_counts_by_time_period,
    get_error_categories_summary,
    get_performance_metrics,
    get_component_reliability,
    get_db_connection,
    create_position_tracking_tables,
    initialize_sem_positions,
    initialize_tem_positions,
    get_system_state,
    get_sem_positions,
    get_tem_positions,
    clear_sem_positions,
    clear_tem_positions,
    #update_sem_position,
    update_system_state,
    get_last_process_result,
    init_soak_test_database,
    create_soak_test_session,
    update_soak_test_session,
    get_soak_test_session,
    get_current_soak_test_session,
    create_soak_test_cycle,
    log_soak_test_operation,
    log_soak_test_error,
    get_soak_test_statistics,
    get_recent_soak_tests,
    get_soak_test_errors_for_session,
    determine_soak_error_category
)

app = Flask(__name__)
# Eventlet was added because it was lagging the server. Adding it fixed the lag. Install with "pip install eventlet"
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", logger=True, engineio_logger=True)

# Define IPs and ports, and 3DP COM port
host_ip = "0.0.0.0"  # Set to listen on all interfaces
web_port = 8000
udp_port = 8001
server_ip = "192.168.1.1"
plc_ip = '192.168.1.172'
plc_port = 8888
c3dp_com_port = "COM7"

# Define numeric values for linear actuators
sem_stage_opened = "040"
sem_stage_closed = "150"
tem_grid_holder_opened = "100"
tem_grid_holder_closed = "158"
rotator_faceDown = "020"
rotator_faceUp = "155"
gripper_home = "000" #gripper fully open
gripper_close = "106" #gripper closed enough to firmly hold a stub
gripper_stub_release = "084" #gripper opened just enough to release a stub
gripper_stub_press = "094" #gripper closed enough to press a stub down without contamining the carbon tape


# Define 3D printer constants for safe operation - be careful when changing or the machine might break
MEASURED_BASE_HEIGHT = 71 #71 is the measured value that David measured
MAX_EXPOSURE_DISTANCE = 25.0
SPEED_VLOW = 0.005
SPEED_LOW = 0.02
SPEED_NORMAL = 0.5
PAUSE = 2
PAUSE_VAC = 3

# Define variables for physical control of the 3D printer
global_robot = None
tem_manual_state = "idle"
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
#phenom_handler_filename = 'phenom_handler.csv' # TODO - delete this?
stubs_tray_filename = 'stubs_tray.csv'

current_process_runs = {}
soak_test_in_progress = False
current_soak_test_session = None
current_soak_test_thread = None
soak_test_stop_event = threading.Event()

def initialize_app():
    """Initialize the application including database setup."""
    try:
        print("Initializing EM Autoprep application...")
        init_database()
        create_position_tracking_tables()
        initialize_sem_positions()
        initialize_tem_positions()
        init_soak_test_database()
        print("Application initialization completed successfully")
    except Exception as e:
        print(f"Error during application initialization: {e}")
        log_standalone_error(f"Application initialization failed: {e}", "system")

def float_or_none(s):
  if s == 'None':
    return None
  return float(s)

def read_CSV_into_positions(path): 
  positions = {}
  with open(path, mode ='r') as file:
    csvFile = csv.reader(file)
    in_metadata = True
    has_data = False
    
    for lines in csvFile:
      if not lines:  # Skip empty lines
        continue
        
      # Skip comment lines
      if lines[0].strip().startswith('#'):
        continue
        
      # Check for end of metadata marker
      if lines[0].strip() == "---":
        in_metadata = False
        continue
        
      # Skip metadata section
      if in_metadata:
        # If line looks like data (has enough columns and first isn't a comment), 
        # assume no metadata section and process it
        if len(lines) >= 4 and not lines[0].strip().startswith('#'):
          in_metadata = False  # Auto-exit metadata mode
          # Don't continue - fall through to process this line
        else:
          continue
        
      # Process normal data
      if len(lines) >= 4:  # Make sure we have enough columns
        has_data = True
        
        # If there are 5 columns, include the lid position in the tuple
        if len(lines) >= 5:
            positions[lines[0]] = (float_or_none(lines[1]), 
                                  float_or_none(lines[2]), 
                                  float_or_none(lines[3]),
                                  float_or_none(lines[4]))
        else:
            # Just the x, y, z coordinates
            positions[lines[0]] = (float_or_none(lines[1]), 
                                  float_or_none(lines[2]), 
                                  float_or_none(lines[3]))
  
  if not has_data:
    print("Warning: No position data was found in the file.")
    
  return positions

def broadcast(message):
    """Send the same message to both console and socketio."""
    print(message)
    socketio.emit('function_response', {'result': message})

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
    phenom_stub_pos = read_CSV_into_positions(
        path=os.path.join(rootpath, phenom_holder_positions_filename)
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

def c3dp_test_connectivity(complete_test=False, process_run_id=None):
    """
    Consolidated 3D printer connectivity test with database logging.
    This replaces both c3dp_test_connectivity and enhanced_c3dp_test_connectivity functions.
    
    Args:
        complete_test (bool): Whether to perform a complete test with port listing
        process_run_id: Optional process run ID for database logging
    
    Returns:
        tuple: (success: bool, result: str) - success status and detailed message
    """
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
        error_msg = ""
        if complete_test:
            error_msg = f"An error occurred: {var_error}. \n\nAvailable ports:\n"
            try:
                ports = list(serial.tools.list_ports.comports())
                for p in ports:
                    error_msg += f"{p}\n"
            except Exception as port_error:
                error_msg += f"Could not list ports: {port_error}\n"
        else:
            error_msg = f"Could not connect to printer: {var_error}"
        
        # Log the connectivity failure
        if process_run_id:
            log_error(process_run_id, f"3D printer connectivity test failed: {error_msg}", "robot")
        else:
            log_standalone_error(f"3D printer connectivity test failed: {error_msg}", "robot")
        
        return False, error_msg
    
def c3dp_test_connectivity_machine_test_page():
    """
    Wrapper for machine test page that returns only the result string.
    This maintains compatibility with the machine test page button.
    """
    success, result = c3dp_test_connectivity(complete_test=True)
    return result

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
    return send_plc_command(f"PHLIDMVL{sem_stage_opened}")

def control_panel_sem_stage_partial_open(phenom_stub_lid_value, delay_seconds=7):
    """
    Sends a command to the PLC to partially open the SEM stage and waits for the
    specified delay time to allow the linear actuator to complete its movement.
    
    Args:
        phenom_stub_lid_value: The value for the stub lid position,
                              will be formatted as a 3-digit string.
        delay_seconds (int): The number of seconds to wait after sending the command.
                            Defaults to 7 seconds.
    
    Returns:
        The response from the send_plc_command function.
    """
    # Format the value as a 3-digit string (e.g., 82 becomes "082")
    formatted_value = f"{int(phenom_stub_lid_value):03d}"
    
    # Send the command to the PLC
    response = send_plc_command(f"PHLIDMVL{formatted_value}")
    
    # Wait for the specified delay to allow the actuator to complete its movement
    print(f"Waiting {delay_seconds} seconds for linear actuator movement...")
    time.sleep(delay_seconds)
    
    return response

def control_panel_sem_stage_close():
    return send_plc_command(f"PHLIDMVL{sem_stage_closed}")

def control_panel_gripper_home():
    return send_plc_command(f"SEMSTORG{gripper_home}")

def control_panel_gripper_close():
    return send_plc_command(f"SEMSTORG{gripper_close}")

def control_panel_gripper_release():
    return send_plc_command(f"SEMSTORG{gripper_stub_release}")

def control_panel_gripper_press():
    return send_plc_command(f"SEMSTORG{gripper_stub_press}")

def control_panel_tem_grid_holder_open():
    return send_plc_command(f"TEMPREPL{tem_grid_holder_opened}")

def control_panel_tem_grid_holder_close():
    return send_plc_command(f"TEMPREPL{tem_grid_holder_closed}")

def control_panel_shutdown():
    return send_plc_command("SHUTDWN")

def control_panel_vibration_motor_1_on():
    return send_plc_command("EXPOSURM11")

def control_panel_vibration_motor_1_off():
    return send_plc_command("EXPOSURM10")

def control_panel_vibration_motor_2_on():
    return send_plc_command("EXPOSURM21")

def control_panel_vibration_motor_2_off():
    return send_plc_command("EXPOSURM20")

def control_panel_vibration_motor_both_on():
    return send_plc_command("EXPOSURM31")

def control_panel_vibration_motor_both_off():
    return send_plc_command("EXPOSURM30")

def control_panel_vibration_motor_all_off():
    return send_plc_command("EXPOSURM00")

def control_vibration_motors(motor1_enabled, motor2_enabled, turn_on=True):
    if turn_on:
        if motor1_enabled and motor2_enabled:
            # Both motors enabled - use both on command
            control_panel_vibration_motor_both_on()
        elif motor1_enabled:
            # Only motor 1 enabled
            control_panel_vibration_motor_1_on()
        elif motor2_enabled:
            # Only motor 2 enabled
            control_panel_vibration_motor_2_on()
        # If neither enabled, do nothing
    else:
        # Turn off - always use all off command for safety
        control_panel_vibration_motor_all_off()

def control_panel_rotator(flip_state):
    if flip_state == "faceDown":
        return send_plc_command(f"SEMPREPR{rotator_faceDown}")
    elif flip_state == "faceUp":
        return send_plc_command(f"SEMPREPR{rotator_faceUp}")
    else:
        raise ValueError(f"Invalid flip state: {flip_state}. Expected 'faceUp' or 'faceDown'.")

def control_panel_laser_status():
    return send_plc_command("SEMPREPTEST")

def control_panel_hvps_setting(v,t):
    return send_plc_command(f"EXPOSURV{v}T{t}")

def control_panel_vacuum(destination,status=False):
    if destination == "SEM":
        if status:
            send_plc_command("SEMPREPVAC1")
        else:
            send_plc_command("SEMSTORVAC0")
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
            
            # Retracting the bed after exposure
            if device_retract_bed():
                print("Sample preparation completed successfully.")
                socketio.emit('function_response', {'result': "Sample preparation completed successfully."})
                return True
            else:
                raise Exception("Bed retraction failed")
                
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
    return device_move_bed("extend")

def device_retract_bed():
    return device_move_bed("retract")

def device_move_bed(action):
    def _bed_operation(robot):
        try:
            if action not in ["extend", "retract"]:
                print(f"Invalid action: {action}. Must be 'extend' or 'retract'.")
                socketio.emit('function_response', 
                             {'result': f"Invalid action: {action}. Must be 'extend' or 'retract'."})
                return False
                
            position_name = "BED_EXTENDED" if action == "extend" else "BED_RETRACTED"
            operation_name = "extension" if action == "extend" else "retraction"
            
            print(f"3DP bed {operation_name} requested.")
            socketio.emit('function_response', {'result': f"3DP bed {operation_name} requested."})
            
            # Check if robot is initialized
            if robot is None:
                print("Robot object is not initialized.")
                socketio.emit('function_response', {'result': "Error: Robot is not initialized. Try homing first."})
                return False
            
            # Try to get current position with retries
            position_acquired = False
            retry_count = 0
            max_retries = 3
            
            while not position_acquired and retry_count < max_retries:
                try:
                    print(f"Attempt {retry_count + 1} to get current position...")
                    socketio.emit('function_response', {'result': f"Attempt {retry_count + 1} to get current position..."})
                    robot.get_current_position()
                    current_pos = robot.position
                    print(f"Current position: {current_pos}")
                    position_acquired = True
                except Exception as pos_error:
                    retry_count += 1
                    error_msg = f"Error getting position (attempt {retry_count}): {pos_error}"
                    print(error_msg)
                    socketio.emit('function_response', {'result': error_msg})
                    if retry_count < max_retries:
                        # Try to re-home the robot
                        try:
                            print("Trying to home the robot again...")
                            socketio.emit('function_response', {'result': "Trying to home the robot again..."})
                            robot.gohome()
                            time.sleep(2)  # Give it time to complete
                        except Exception as home_error:
                            print(f"Homing error: {home_error}")
                    else:
                        print("Maximum retries reached. Operation aborted for safety.")
                        socketio.emit('function_response', 
                                    {'result': "Maximum retries reached. Operation aborted for safety reasons."})
                        return False
            
            # Only proceed if we got a valid position
            if position_acquired:
                # Check if Z position is safe
                if current_pos[2] > 15:
                    print("Z position unsafe. Moving to safe position first...")
                    robot.speed = SPEED_NORMAL
                    
                    try:
                        # Move to PRE_EXTEND_POS
                        pre_extend_pos = robot.intermediate_pos["PRE_EXTEND_POS"]
                        robot.moveto(*pre_extend_pos)
                    except Exception as move_error:
                        print(f"Error accessing positions or moving: {move_error}")
                        socketio.emit('function_response', 
                                    {'result': f"Error during movement: {move_error}. Operation aborted for safety."})
                        return False
                else:
                    # Z is safe, just ensure X is at safe position
                    print("Z position safe. Moving X to safe position...")
                    robot.speed = SPEED_NORMAL
                    print(f"Moving to ({15}, {current_pos[1]}, {current_pos[2]})")
                    try:
                        robot.moveto(15, current_pos[1], current_pos[2])
                    except Exception as move_error:
                        print(f"Error moving to safe X position: {move_error}")
                        socketio.emit('function_response', 
                                    {'result': f"Error during movement: {move_error}. Operation aborted for safety."})
                        return False
                
                # Now move the bed in the requested direction
                print(f"Moving to {position_name} position...")
                try:
                    # Move to the target position
                    target_position = robot.intermediate_pos[position_name]
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*target_position)
                except Exception as op_error:
                    print(f"Error during bed {operation_name}: {op_error}")
                    socketio.emit('function_response', 
                                {'result': f"Error during bed {operation_name}: {op_error}. Operation aborted."})
                    return False
                
                print(f"3DP bed {operation_name} completed successfully")
                socketio.emit('function_response', {'result': f"3DP bed {operation_name} completed successfully."})
                return True
            else:
                # We should never reach here, but just in case
                print("Failed to get position after retries. Operation aborted for safety.")
                socketio.emit('function_response', 
                            {'result': "Failed to get position. Operation aborted for safety reasons."})
                return False
                
        except Exception as e:
            error_message = f"3DP bed {operation_name} failed: {e}"
            print(error_message)
            socketio.emit('function_response', {'result': error_message})
            return False
            
    # Initialize robot if needed through handle_robot_operation
    # This ensures robot is properly initialized before we try to use it
    return handle_robot_operation(
        _bed_operation,
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

    return handle_robot_operation(_move_operation, robot=global_robot)

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

    return handle_robot_operation(_home_operation, robot=global_robot)
''' Backup of the previous SEM process action before position tracking
def sem_process_action(voltage, c_height, distance, etime, origin, destination, process_run_id=None, motor1_enabled=False, motor2_enabled=False):
    def _sem_operation(robot, voltage, c_height, distance, etime, origin, destination, motor1_enabled, motor2_enabled, process_run_id):
        # Initialize success flag
        process_successful = False
        
        try:
            # Format voltage and time to 5 characters with leading zeros
            voltage_formatted = f"{int(voltage):05d}"
            etime_formatted = f"{int(etime):05d}"

            print(f"SEM process requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}, vibMotor1={motor1_enabled}, vibMotor2={motor2_enabled}")
            socketio.emit('function_response', {'result': f"SEM process requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}, vibMotor1={motor1_enabled}, vibMotor2={motor2_enabled}"})

            # Step 1: Home robot
            try:
                robot.gohome()
            except Exception as var_error:
                error_msg = f"An error occurred when trying to home robot: {var_error}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

            # Step 2: Navigate to intermediate position
            try:
                robot.speed = SPEED_NORMAL
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                print(f"Collecting stub from {origin}")
                socketio.emit('function_response', {'result': f"Collecting stub from {origin}."})
            except Exception as e:
                error_msg = f"Error moving to intermediate position: {e}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

            # Step 3: Stub collection with improved error handling
            stub_pick_trials = 0
            stub_picked = False
            
            try:
                control_panel_vacuum("SEM", True)
            except Exception as e:
                error_msg = f"Error enabling vacuum: {e}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "PLC")
                return False
            
            while stub_pick_trials <= 2:  # Changed condition for clarity
                try:
                    print("Trying to pick the stub...")
                    socketio.emit('function_response', {'result': "Trying to pick the stub..."})

                    # Stub picking sequence
                    robot.moveto(*robot.clean_stub_pos[origin])
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z1"])
                    robot.speed = SPEED_LOW
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z3"])
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    
                    print("Checking if stub was picked...")
                    socketio.emit('function_response', {'result': "Checking if stub was picked..."})
                    
                    # Move to laser detection position
                    robot.moveto(*robot.equipment_pos["LASER_SEM"])
                    robot.moveto(*robot.equipment_pos["LASER_SEM_Z1"])

                    # Check if stub was picked
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
                        stub_pick_trials += 1
                        
                except Exception as e:
                    error_msg = f"Error during stub picking attempt {stub_pick_trials + 1}: {e}"
                    print(error_msg)
                    socketio.emit('function_response', {'result': error_msg})
                    # Log specific error for each attempt
                    if process_run_id:
                        log_error(process_run_id, error_msg, "robot")
                    stub_pick_trials += 1
                    
                    # Try to recover to safe position
                    try:
                        robot.speed = SPEED_NORMAL
                        robot.moveto(*robot.intermediate_pos["ZHOME"])
                    except:
                        pass  # If recovery fails, we'll catch it in the outer try-except

            # Check if stub picking failed
            if not stub_picked:
                error_msg = "Stub not picked after 3 attempts. Process failed."
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error for stub picking failure
                if process_run_id:
                    log_error(process_run_id, error_msg, "process")
                try:
                    control_panel_vacuum("SEM", False)
                except:
                    pass
                return False  # Explicitly return False for failed stub picking

            # Step 4: Charging and exposure process
            try:
                # Move to charger and position for exposure
                robot.moveto(*robot.equipment_pos["CHARGER_SEM"])
                robot.moveto(z=MEASURED_BASE_HEIGHT - int(c_height))
                socketio.emit('function_response', {'result': f"Setting at: {MEASURED_BASE_HEIGHT - int(c_height)} mm."})
                robot.moveto(z=MEASURED_BASE_HEIGHT - int(c_height) + int(distance))
                socketio.emit('function_response', {'result': f"Exposing at: {MEASURED_BASE_HEIGHT - int(c_height) + int(distance)} mm."})

                # VIBRATION MOTOR INTEGRATION - Turn on motors before exposure
                if motor1_enabled or motor2_enabled:
                    try:
                        socketio.emit('function_response', {'result': "Turning on vibration motors..."})
                        control_vibration_motors(motor1_enabled, motor2_enabled, turn_on=True)
                        time.sleep(0.5)  # Brief delay to ensure motors are running
                    except Exception as e:
                        error_msg = f"Warning: Error controlling vibration motors: {e}"
                        print(error_msg)
                        socketio.emit('function_response', {'result': error_msg})
                        # Log motor error but continue with process
                        if process_run_id:
                            log_error(process_run_id, error_msg, "PLC")

                # Perform exposure
                print(f"Stub will be exposed to {voltage} kV for {etime} ms.")
                socketio.emit('function_response', {'result': f"Stub will be exposed to {voltage} kV for {etime} ms."})
                control_panel_hvps_setting(voltage_formatted, etime_formatted)
                time.sleep(int(etime)/1000+2)
                
                # VIBRATION MOTOR INTEGRATION - Turn off motors after exposure
                if motor1_enabled or motor2_enabled:
                    try:
                        socketio.emit('function_response', {'result': "Turning off vibration motors..."})
                        control_vibration_motors(motor1_enabled, motor2_enabled, turn_on=False)
                    except Exception as e:
                        error_msg = f"Warning: Error turning off vibration motors: {e}"
                        print(error_msg)
                        socketio.emit('function_response', {'result': error_msg})
                        # Log motor error but continue
                        if process_run_id:
                            log_error(process_run_id, error_msg, "PLC")

                robot.moveto(*robot.intermediate_pos["ZHOME"])
                
            except Exception as e:
                error_msg = f"Error during charging/exposure process: {e}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "process")
                # Ensure motors are turned off
                try:
                    if motor1_enabled or motor2_enabled:
                        control_panel_vibration_motor_all_off()
                except:
                    pass
                return False

            # Step 5: Delivery to destination
            try:
                if destination == "tray":
                    print(f"Delivering stub to tray: {origin}.")
                    socketio.emit('function_response', {'result': f"Delivering stub to tray: {origin}."})
                    robot.moveto(*robot.clean_stub_pos[origin])
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z1"])
                    robot.speed = SPEED_LOW
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z3"])
                    control_panel_vacuum("SEM", False)
                    time.sleep(PAUSE_VAC)
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    robot.moveto(x=robot.intermediate_pos["HOME"][0])
                    robot.moveto(y=robot.intermediate_pos["HOME"][1])
                else:
                    print(f"Delivering stub to stage: {destination}.")
                    socketio.emit('function_response', {'result': f"Delivering stub to stage: {destination}."})
                    #homing rotator
                    control_panel_rotator("faceDown")
                    #homing gripper
                    control_panel_gripper_home()

                    robot.moveto(*robot.equipment_pos["ROTATOR_0"])
                    robot.moveto(*robot.equipment_pos["ROTATOR_Z1"])
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.equipment_pos["ROTATOR_ENGAGE"])
                    control_panel_vacuum("SEM", False)
                    time.sleep(PAUSE_VAC)
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    #rotating stub
                    control_panel_rotator("faceUp")
                    robot.moveto(*robot.equipment_pos["GRIPPER_ROTATOR_0"])
                    robot.moveto(*robot.equipment_pos["GRIPPER_ROTATOR_Z1"])
                    #closing gripper on the stub
                    control_panel_gripper_close()
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.equipment_pos["GRIPPER_ROTATOR_DISENGAGE"])
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    #homing rotator
                    control_panel_rotator("faceDown")
                    #opening stage lid
                    control_panel_sem_stage_partial_open(int(robot.phenom_stub_pos[destination][4]))
                    robot.moveto(*robot.phenom_stub_pos[destination])
                    robot.moveto(*robot.phenom_stub_pos["PH_Z1"])
                    robot.speed = SPEED_LOW
                    robot.moveto(*robot.phenom_stub_pos["PH_Z2"])
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.phenom_stub_pos["PH_Z3"])
                    #opening gripper, partially, enough to release stub
                    control_panel_gripper_release()
                    robot.moveto(*robot.phenom_stub_pos["PH_Z4"])
                    #closing gripper, partially, to press stub down
                    control_panel_gripper_press()
                    robot.moveto(*robot.phenom_stub_pos["PH_Z5"])
                    #opening gripper
                    control_panel_gripper_home()
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    control_panel_sem_stage_close()
                    #homing in X and Y only so the machine doesn't do two bed retractions
                    robot.moveto(x=robot.intermediate_pos["HOME"][0])
                    robot.moveto(y=robot.intermediate_pos["HOME"][1])

            except Exception as e:
                error_msg = f"Error during delivery process: {e}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

            # Step 6: Final cleanup
            try:
                device_step_final(robot)
                process_successful = True  # Only set to True if we reach this point
                print("SEM process completed successfully.")
                socketio.emit('function_response', {'result': "SEM process completed successfully."})
                return True
                
            except Exception as e:
                error_msg = f"Error in final cleanup: {e}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

        except Exception as e:
            error_msg = f"Unexpected error in SEM process: {e}"
            print(error_msg)
            socketio.emit('function_response', {'result': error_msg})
            # Log unexpected error
            if process_run_id:
                log_error(process_run_id, error_msg, "system")
            # Ensure motors are turned off in case of error
            try:
                if motor1_enabled or motor2_enabled:
                    control_panel_vibration_motor_all_off()
            except:
                pass
            return False

    # Modified wrapper call to pass process_run_id through
    def _wrapper_with_logging():
        return handle_robot_operation(
            lambda robot: _sem_operation(
                robot, voltage, c_height, distance, etime, 
                origin, destination, motor1_enabled, motor2_enabled, process_run_id
            ),
            robot=global_robot
        )
    
    # Call the operation with proper error handling
    result = handle_control_panel_operation(_wrapper_with_logging)
    
    # Additional logging for wrapper failures (PLC/robot connection issues)
    if result is False and process_run_id:
        # This catches cases where handle_control_panel_operation or handle_robot_operation fail
        log_error(process_run_id, "Process failed due to control panel or robot connection issues", "system")
    
    # Ensure we return the actual result
    return result
'''
def sem_process_action(voltage, c_height, distance, etime, origin, destination, process_run_id=None, motor1_enabled=False, motor2_enabled=False):
    """
    Enhanced SEM process action with position tracking integration.
    
    Args:
        voltage: Exposure voltage
        c_height: Container height  
        distance: Vertical shift
        etime: Exposure time
        origin: Origin position (e.g., 'A1')
        destination: Destination position (e.g., 'A2' or 'tray')
        process_run_id: Optional process run ID for database logging
        motor1_enabled: Enable vibration motor 1
        motor2_enabled: Enable vibration motor 2
    
    Returns:
        Success or error message
    """

    # Format voltage and time to 5 characters with leading zeros
    voltage_formatted = f"{int(voltage):05d}"
    etime_formatted = f"{int(etime):05d}"

    broadcast(f"SEM process requested with parameters: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}, vibMotor1={motor1_enabled}, vibMotor2={motor2_enabled}")
    
    def get_position_status(position_type, position_name):
        """Get the current status of a specific position."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                if position_type == 'sem':
                    cursor.execute("""
                        SELECT status FROM sem_positions 
                        WHERE position_name = ?
                    """, (position_name,))
                    
                    result = cursor.fetchone()
                    return result[0] if result else 'unknown'
                    
        except Exception as e:
            print(f"Error getting position status: {e}")
            return 'unknown'
    
    def update_position_status(position_type, position_name, new_status):
        """Update the status of a specific position."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                if position_type == 'sem':
                    cursor.execute("""
                        UPDATE sem_positions 
                        SET status = ?, last_updated = CURRENT_TIMESTAMP
                        WHERE position_name = ?
                    """, (new_status, position_name))
                    
                    conn.commit()
                    print(f"Updated SEM position {position_name} to status: {new_status}")
                    return True
                    
        except Exception as e:
            print(f"Error updating position status: {e}")
            return False
    
    # POSITION VALIDATION - Check both origin and destination before starting
    try:
        # Check origin position availability
        origin_status = get_position_status('sem', origin)
        if origin_status != 'clean': 
            error_msg = f"ERROR: Origin position {origin} does not contain a clean stub (current status: {origin_status}). Please verify sample tracking on your end."
            
            # Log the validation error
            if process_run_id:
                log_error(process_run_id, error_msg, "user")
            else:
                log_standalone_error(error_msg, "user")
            
            return error_msg
        
        # Check destination position availability (only if not returning to same tray)
        if destination != "tray":
            destination_status = get_position_status('sem', destination)
            if destination_status != 'empty':
                error_msg = f"ERROR: Destination position {destination} is already occupied (current status: {destination_status}). Please verify sample tracking on your end."
                broadcast(error_msg)
                
                # Log the validation error
                if process_run_id:
                    log_error(process_run_id, error_msg, "user")
                else:
                    log_standalone_error(error_msg, "user")
                
                return error_msg
        
    except Exception as e:
        error_msg = f"ERROR: Failed to validate positions: {str(e)}"
        broadcast(error_msg)
        
        if process_run_id:
            log_error(process_run_id, error_msg, "system")
        else:
            log_standalone_error(error_msg, "system")
        
        return error_msg
    
    def _sem_operation(robot, voltage, c_height, distance, etime, origin, destination, motor1_enabled, motor2_enabled, process_run_id):
        # Initialize success flag
        process_successful = False
        
        try:
            # Step 1: Home the robot first
            try:
                robot.gohome()
            except Exception as e:
                error_msg = f"Error during homing: {e}"
                broadcast(error_msg)
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

            # Step 2: Move to intermediate position
            try:
                robot.speed = SPEED_NORMAL
                robot.moveto(*robot.intermediate_pos["ZHOME"])
            except Exception as e:
                error_msg = f"Error moving to intermediate position: {e}"
                broadcast(error_msg)
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

            # Step 3: Stub collection with improved error handling
            stub_pick_trials = 0
            stub_picked = False
            
            try:
                control_panel_vacuum("SEM", True)
            except Exception as e:
                error_msg = f"Error enabling vacuum: {e}"
                broadcast(error_msg)
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "PLC")
                return False
            
            while stub_pick_trials <= 2:  # Changed condition for clarity
                try:
                    broadcast("Trying to pick the stub...")

                    # Stub picking sequence
                    robot.moveto(*robot.clean_stub_pos[origin])
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z1"])
                    robot.speed = SPEED_LOW
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z3"])
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    
                    broadcast("Checking if stub was picked...")
                    
                    # Move to laser detection position
                    robot.moveto(*robot.equipment_pos["LASER_SEM"])
                    robot.moveto(*robot.equipment_pos["LASER_SEM_Z1"])

                    # Check if stub was picked
                    if control_panel_laser_status() == "LASER1":
                        broadcast("Stub was picked!")
                        stub_picked = True
                        robot.moveto(*robot.intermediate_pos["ZHOME"])
                        break
                    else:
                        broadcast("Stub was not detected. Trying again...")
                        robot.moveto(*robot.intermediate_pos["ZHOME"])
                        stub_pick_trials += 1
                        
                except Exception as e:
                    error_msg = f"Error during stub picking attempt {stub_pick_trials + 1}: {e}"
                    broadcast(error_msg)
                    # Log specific error for each attempt
                    if process_run_id:
                        log_error(process_run_id, error_msg, "robot")
                    stub_pick_trials += 1
                    
                    # Try to recover to safe position
                    try:
                        robot.speed = SPEED_NORMAL
                        robot.moveto(*robot.intermediate_pos["ZHOME"])
                    except:
                        pass  # If recovery fails, we'll catch it in the outer try-except

            # Check if stub picking failed
            if not stub_picked:
                error_msg = "Stub not picked after 3 attempts. Process failed."
                broadcast(error_msg)
                # Log specific error for stub picking failure
                if process_run_id:
                    log_error(process_run_id, error_msg, "process")
                try:
                    control_panel_vacuum("SEM", False)
                except:
                    pass
                return False  # Explicitly return False for failed stub picking

            # Step 4: Charging and exposure process
            try:
                # Move to charger and position for exposure
                robot.moveto(*robot.equipment_pos["CHARGER_SEM"])
                robot.moveto(z=MEASURED_BASE_HEIGHT - int(c_height))
                broadcast(f"Setting at: {MEASURED_BASE_HEIGHT - int(c_height)} mm.")
                robot.moveto(z=MEASURED_BASE_HEIGHT - int(c_height) + int(distance))
                broadcast(f"Exposing at: {MEASURED_BASE_HEIGHT - int(c_height) + int(distance)} mm.")

                # VIBRATION MOTOR INTEGRATION - Turn on motors before exposure
                if motor1_enabled or motor2_enabled:
                    try:
                        broadcast("Turning on vibration motors...")
                        control_vibration_motors(motor1_enabled, motor2_enabled, turn_on=True)
                    except Exception as e:
                        error_msg = f"Warning: Error turning on vibration motors: {e}"
                        broadcast(error_msg)
                        # Log motor error but continue
                        if process_run_id:
                            log_error(process_run_id, error_msg, "PLC")

                # Actual charging and exposure
                broadcast(f"Stub will be exposed to {voltage} kV for {etime} ms.")
                control_panel_hvps_setting(voltage_formatted, etime_formatted)
                time.sleep(int(etime)/1000+2)
                
                # VIBRATION MOTOR INTEGRATION - Turn off motors after exposure
                if motor1_enabled or motor2_enabled:
                    try:
                        broadcast("Turning off vibration motors...")
                        control_vibration_motors(motor1_enabled, motor2_enabled, turn_on=False)
                    except Exception as e:
                        error_msg = f"Warning: Error turning off vibration motors: {e}"
                        broadcast(error_msg)
                        # Log motor error but continue
                        if process_run_id:
                            log_error(process_run_id, error_msg, "PLC")

                robot.moveto(*robot.intermediate_pos["ZHOME"])
                
            except Exception as e:
                error_msg = f"Error during charging/exposure process: {e}"
                broadcast(error_msg)
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "process")
                # Ensure motors are turned off
                try:
                    if motor1_enabled or motor2_enabled:
                        control_panel_vibration_motor_all_off()
                except:
                    pass
                return False

            # Step 5: Delivery to destination
            try:
                if destination == "tray":
                    broadcast(f"Delivering stub to tray: {origin}.")
                    robot.moveto(*robot.clean_stub_pos[origin])
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z1"])
                    robot.speed = SPEED_LOW
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z3"])
                    control_panel_vacuum("SEM", False)
                    time.sleep(PAUSE_VAC)
                    robot.moveto(*robot.clean_stub_pos["STRAY_Z2"])
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    robot.moveto(x=robot.intermediate_pos["HOME"][0])
                    robot.moveto(y=robot.intermediate_pos["HOME"][1])
                else:
                    broadcast(f"Delivering stub to stage: {destination}.")
                    
                    # Move to destination position
                    robot.moveto(*robot.phenom_stub_pos[destination])
                    robot.moveto(*robot.phenom_stub_pos["PSTAGE_Z1"])
                    robot.speed = SPEED_LOW
                    robot.moveto(*robot.phenom_stub_pos["PSTAGE_Z2"])
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.phenom_stub_pos["PSTAGE_Z3"])
                    control_panel_vacuum("SEM", False)
                    time.sleep(PAUSE_VAC)
                    robot.moveto(*robot.phenom_stub_pos["PSTAGE_Z2"])
                    robot.speed = SPEED_NORMAL

                    # Opening gripper
                    control_panel_gripper_home()
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    control_panel_sem_stage_close()
                    # Homing in X and Y only so the machine doesn't do two bed retractions
                    robot.moveto(x=robot.intermediate_pos["HOME"][0])
                    robot.moveto(y=robot.intermediate_pos["HOME"][1])

            except Exception as e:
                error_msg = f"Error during delivery process: {e}"
                broadcast(error_msg)
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

            # Step 6: Final cleanup
            try:
                device_step_final(robot)
                process_successful = True  # Only set to True if we reach this point
                broadcast("SEM process completed successfully.")
                return True
                
            except Exception as e:
                error_msg = f"Error in final cleanup: {e}"
                broadcast(error_msg)
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

        except Exception as e:
            error_msg = f"Unexpected error in SEM process: {e}"
            broadcast(error_msg)
            # Log unexpected error
            if process_run_id:
                log_error(process_run_id, error_msg, "system")
            # Ensure motors are turned off in case of error
            try:
                if motor1_enabled or motor2_enabled:
                    control_panel_vibration_motor_all_off()
            except:
                pass
            return False

    # Modified wrapper call to pass process_run_id through
    def _wrapper_with_logging():
        return handle_robot_operation(
            lambda robot: _sem_operation(
                robot, voltage, c_height, distance, etime, 
                origin, destination, motor1_enabled, motor2_enabled, process_run_id
            ),
            robot=global_robot
        )
    
    # Call the operation with proper error handling
    result = handle_control_panel_operation(_wrapper_with_logging)
    
    # POSITION TRACKING UPDATE - Only update positions on successful completion
    if result is True:
        try:
            # Update origin position to empty (stub was taken)
            update_position_status('sem', origin, 'empty')
            
            # Update destination position based on where stub was delivered
            if destination == "tray":
                # Stub returned to same tray position as occupied
                update_position_status('sem', origin, 'occupied')
            else:
                # Stub delivered to stage position
                update_position_status('sem', destination, 'occupied')
            
            print(f"Position tracking updated successfully: {origin} -> empty, {destination if destination != 'tray' else origin} -> occupied")
                
        except Exception as e:
            error_msg = f"Warning: Process completed but position tracking update failed: {str(e)}"
            broadcast(error_msg)
            
            # Log the tracking error but don't fail the process
            if process_run_id:
                log_error(process_run_id, error_msg, "system")
            else:
                log_standalone_error(error_msg, "system")
    
    # Additional logging for wrapper failures (PLC/robot connection issues)
    if result is False and process_run_id:
        # This catches cases where handle_control_panel_operation or handle_robot_operation fail
        log_error(process_run_id, "Process failed due to control panel or robot connection issues", "system")
    
    # Ensure we return the actual result
    return result

def tem_process_action(voltage, c_height, distance, etime, origin, destination, skip_laser=False, process_run_id=None, motor1_enabled=False, motor2_enabled=False):
    def _tem_operation(robot, voltage, c_height, distance, etime, origin, destination, skip_laser, motor1_enabled, motor2_enabled, process_run_id):
        # Initialize success flag
        process_successful = False
        
        try:
            # Format voltage and time to 5 characters with leading zeros
            voltage_formatted = f"{int(voltage):05d}"
            etime_formatted = f"{int(etime):05d}"

            print(f"TEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}, vibMotor1={motor1_enabled}, vibMotor2={motor2_enabled}")
            socketio.emit('function_response', {'result': f"TEM TRAY requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}, origin={origin}, destination={destination}, vibMotor1={motor1_enabled}, vibMotor2={motor2_enabled}"})

            # Step 1: Home robot
            try:
                robot.gohome()
            except Exception as var_error:
                error_msg = f"An error occurred when trying to home robot: {var_error}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

            # Step 2: Navigate to intermediate position
            try:
                robot.speed = SPEED_NORMAL
                robot.moveto(*robot.intermediate_pos["ZHOME"])
                print(f"Collecting grid from {origin}")
                socketio.emit('function_response', {'result': f"Collecting grid from {origin}."})
            except Exception as e:
                error_msg = f"Error moving to intermediate position: {e}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

            # Step 3: Grid collection with improved error handling
            grid_pick_trials = 0
            grid_picked = False

            if skip_laser:
                # Skip laser verification path
                try:
                    print("Skipping laser verification - assuming grid was picked successfully")
                    socketio.emit('function_response', {'result': "Skipping laser verification - assuming grid was picked successfully"})
                    
                    robot.moveto(x=robot.clean_disk_pos[origin][0])
                    control_panel_tem_grid_holder_open()
                    time.sleep(1.5)
                    control_panel_vacuum("TEM", True)
                    robot.moveto(*robot.clean_disk_pos[origin])
                    robot.moveto(*robot.clean_disk_pos["TCTRAY_Z1"])
                    robot.speed = SPEED_LOW
                    robot.moveto(*robot.clean_disk_pos["TCTRAY_Z2"])
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.clean_disk_pos["TCTRAY_Z3"])
                    robot.moveto(*robot.clean_disk_pos["TCTRAY_Z2"])
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    
                    grid_picked = True
                    
                except Exception as e:
                    error_msg = f"Error during grid collection (skip laser mode): {e}"
                    print(error_msg)
                    socketio.emit('function_response', {'result': error_msg})
                    # Log specific error
                    if process_run_id:
                        log_error(process_run_id, error_msg, "robot")
                    # Clean up on error
                    try:
                        control_panel_vacuum("TEM", False)
                        control_panel_tem_grid_holder_close()
                    except:
                        pass
                    return False
            else:
                # Normal grid collection with laser verification
                try:
                    control_panel_vacuum("TEM", True)
                except Exception as e:
                    error_msg = f"Error enabling TEM vacuum: {e}"
                    print(error_msg)
                    socketio.emit('function_response', {'result': error_msg})
                    # Log specific error
                    if process_run_id:
                        log_error(process_run_id, error_msg, "PLC")
                    return False
                
                while grid_pick_trials <= 2:  # Changed condition for clarity
                    try:
                        print("Trying to pick the grid...")
                        socketio.emit('function_response', {'result': "Trying to pick the grid..."})

                        # Grid picking sequence
                        robot.moveto(x=robot.clean_disk_pos[origin][0])
                        control_panel_tem_grid_holder_open()
                        time.sleep(1.5)
                        robot.moveto(*robot.clean_disk_pos[origin])
                        robot.moveto(*robot.clean_disk_pos["TCTRAY_Z1"])
                        robot.speed = SPEED_LOW
                        robot.moveto(*robot.clean_disk_pos["TCTRAY_Z2"])
                        robot.speed = SPEED_VLOW
                        robot.moveto(*robot.clean_disk_pos["TCTRAY_Z3"])
                        robot.moveto(*robot.clean_disk_pos["TCTRAY_Z2"])
                        robot.speed = SPEED_NORMAL
                        robot.moveto(*robot.intermediate_pos["ZHOME"])
                        
                        print("Checking if grid was picked...")
                        socketio.emit('function_response', {'result': "Checking if grid was picked..."})
                        
                        # Move to laser detection position
                        robot.moveto(*robot.equipment_pos["LASER_TEM"])
                        robot.moveto(*robot.equipment_pos["LASER_TEM_Z1"])

                        # Check if grid was picked
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
                            grid_pick_trials += 1
                            
                    except Exception as e:
                        error_msg = f"Error during grid picking attempt {grid_pick_trials + 1}: {e}"
                        print(error_msg)
                        socketio.emit('function_response', {'result': error_msg})
                        # Log specific error for each attempt
                        if process_run_id:
                            log_error(process_run_id, error_msg, "robot")
                        grid_pick_trials += 1
                        
                        # Try to recover to safe position
                        try:
                            robot.speed = SPEED_NORMAL
                            robot.moveto(*robot.intermediate_pos["ZHOME"])
                        except:
                            pass  # If recovery fails, we'll catch it in the outer try-except

                # Check if grid picking failed after all attempts
                if not grid_picked:
                    error_msg = "Grid not picked after 3 attempts. Process failed."
                    print(error_msg)
                    socketio.emit('function_response', {'result': error_msg})
                    # Log specific error for grid picking failure
                    if process_run_id:
                        log_error(process_run_id, error_msg, "process")
                    # Clean up on failure
                    try:
                        control_panel_vacuum("TEM", False)
                        robot.moveto(*robot.intermediate_pos["ZHOME"])
                        time.sleep(1)
                        control_panel_tem_grid_holder_close()
                        time.sleep(1.5)
                    except:
                        pass
                    return False  # Explicitly return False for failed grid picking

            # Step 4: Charging and exposure process
            if grid_picked:
                try:
                    # Move to charger and position for exposure
                    robot.moveto(*robot.equipment_pos["CHARGER_TEM"])
                    robot.moveto(z=MEASURED_BASE_HEIGHT - int(c_height))
                    socketio.emit('function_response', {'result': f"Setting at: {MEASURED_BASE_HEIGHT - int(c_height)} mm."})
                    robot.moveto(z=MEASURED_BASE_HEIGHT - int(c_height) + int(distance))
                    socketio.emit('function_response', {'result': f"Exposing at: {MEASURED_BASE_HEIGHT - int(c_height) + int(distance)} mm."})

                    # VIBRATION MOTOR INTEGRATION - Turn on motors before exposure
                    if motor1_enabled or motor2_enabled:
                        try:
                            socketio.emit('function_response', {'result': "Turning on vibration motors..."})
                            control_vibration_motors(motor1_enabled, motor2_enabled, turn_on=True)
                            time.sleep(0.5)  # Brief delay to ensure motors are running
                        except Exception as e:
                            error_msg = f"Warning: Error controlling vibration motors: {e}"
                            print(error_msg)
                            socketio.emit('function_response', {'result': error_msg})
                            # Log motor error but continue with process
                            if process_run_id:
                                log_error(process_run_id, error_msg, "PLC")

                    # Perform exposure
                    print(f"Grid will be exposed to {voltage} kV for {etime} ms.")
                    socketio.emit('function_response', {'result': f"Grid will be exposed to {voltage} kV for {etime} ms."})
                    control_panel_hvps_setting(voltage_formatted, etime_formatted)
                    time.sleep(int(etime)/1000+2)
                    
                    # VIBRATION MOTOR INTEGRATION - Turn off motors after exposure
                    if motor1_enabled or motor2_enabled:
                        try:
                            print(f"Turning off vibration motors...")
                            socketio.emit('function_response', {'result': "Turning off vibration motors..."})
                            control_vibration_motors(motor1_enabled, motor2_enabled, turn_on=False)
                        except Exception as e:
                            error_msg = f"Warning: Error turning off vibration motors: {e}"
                            print(error_msg)
                            socketio.emit('function_response', {'result': error_msg})
                            # Log motor error but continue
                            if process_run_id:
                                log_error(process_run_id, error_msg, "PLC")

                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    
                except Exception as e:
                    error_msg = f"Error during charging/exposure process: {e}"
                    print(error_msg)
                    socketio.emit('function_response', {'result': error_msg})
                    # Log specific error
                    if process_run_id:
                        log_error(process_run_id, error_msg, "process")
                    # Ensure motors are turned off
                    try:
                        if motor1_enabled or motor2_enabled:
                            control_panel_vibration_motor_all_off()
                    except:
                        pass
                    return False

                # Step 5: Delivery to destination
                try:
                    print(f"Delivering grid to {destination}.")
                    socketio.emit('function_response', {'result': f"Delivering grid to {destination}."})
                    
                    # Moving X and Y separately to ensure the grid never passes over another grid to avoid cross-contamination
                    robot.moveto(x=robot.used_disk_pos[destination][0])
                    control_panel_tem_grid_holder_open()
                    time.sleep(1)
                    robot.moveto(y=robot.used_disk_pos[destination][1])
                    robot.moveto(*robot.used_disk_pos["TETRAY_Z1"])
                    robot.speed = SPEED_LOW
                    robot.moveto(*robot.used_disk_pos["TETRAY_Z2"])
                    robot.speed = SPEED_VLOW
                    robot.moveto(*robot.used_disk_pos["TETRAY_Z3"])
                    control_panel_vacuum("TEM", False)
                    time.sleep(PAUSE_VAC)
                    robot.moveto(*robot.used_disk_pos["TETRAY_Z2"])
                    robot.speed = SPEED_NORMAL
                    robot.moveto(*robot.intermediate_pos["ZHOME"])
                    time.sleep(1)
                    control_panel_tem_grid_holder_close()
                    time.sleep(1)
                    # Homing in X and Y only so the machine doesn't do two bed retractions
                    robot.moveto(x=robot.intermediate_pos["HOME"][0])
                    robot.moveto(y=robot.intermediate_pos["HOME"][1])

                except Exception as e:
                    error_msg = f"Error during delivery process: {e}"
                    print(error_msg)
                    socketio.emit('function_response', {'result': error_msg})
                    # Log specific error
                    if process_run_id:
                        log_error(process_run_id, error_msg, "robot")
                    return False

            # Step 6: Final cleanup
            try:
                device_step_final(robot)
                process_successful = True  # Only set to True if we reach this point
                print("TEM process completed successfully.")
                socketio.emit('function_response', {'result': "TEM process completed successfully."})
                return True
                
            except Exception as e:
                error_msg = f"Error in final cleanup: {e}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                # Log specific error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                return False

        except Exception as e:
            error_msg = f"Unexpected error in TEM process: {e}"
            print(error_msg)
            socketio.emit('function_response', {'result': error_msg})
            # Log unexpected error
            if process_run_id:
                log_error(process_run_id, error_msg, "system")
            # Ensure motors are turned off in case of error
            try:
                if motor1_enabled or motor2_enabled:
                    control_panel_vibration_motor_all_off()
            except:
                pass
            return False

    # Modified wrapper call to pass process_run_id through
    def _wrapper_with_logging():
        return handle_robot_operation(
            lambda robot: _tem_operation(
                robot, voltage, c_height, distance, etime, 
                origin, destination, skip_laser, motor1_enabled, motor2_enabled, process_run_id
            ),
            robot=global_robot
        )
    
    # Call the operation with proper error handling
    result = handle_control_panel_operation(_wrapper_with_logging)
    
    # Additional logging for wrapper failures (PLC/robot connection issues)
    if result is False and process_run_id:
        # This catches cases where handle_control_panel_operation or handle_robot_operation fail
        log_error(process_run_id, "Process failed due to control panel or robot connection issues", "system")
    
    # Ensure we return the actual result
    return result

def tem_manual_prepare():
    """
    Prepare the system for manual TEM grid placement.
    
    This function:
    1. Gets the robot ready
    2. Turns on the TEM vacuum pump
    3. Positions the head at TEM_GRID_MANUAL_MODE position
    
    Returns:
        Success or error message
    """
    global tem_manual_state
    
    # State validation - can only run this if idle
    if tem_manual_state != "idle":
        message = f"Invalid operation: System must be in idle state to prepare, current state: {tem_manual_state}"
        print(message)
        socketio.emit('function_response', {'result': message})
        return message
    
    def _prepare_operation(robot):
        try:
            global tem_manual_state
            
            print("Manual TEM preparation requested")
            socketio.emit('function_response', {'result': "Manual TEM preparation requested"})
            
            # Home the robot first
            try:
                robot.gohome()
            except Exception as var_error:
                print(f"An error occurred during homing: {var_error}")
                socketio.emit('function_response', {'result': f"An error occurred during homing: {var_error}"})
                return False
            
            # Turn on the TEM vacuum pump
            control_panel_vacuum("TEM", True)
            socketio.emit('function_response', {'result': "TEM vacuum pump activated"})
            
            # Position at manual mode position
            robot.speed = SPEED_NORMAL
            robot.moveto(*robot.equipment_pos["TEM_GRID_MANUAL_MODE"])
            robot.moveto(*robot.equipment_pos["TEM_GRID_MANUAL_MODE_Z"])
            socketio.emit('function_response', {'result': "Robot positioned for manual grid placement"})
            
            print("System ready for manual grid placement")
            socketio.emit('function_response', {'result': "STEP 1 COMPLETE: System ready for manual grid placement. Please carefully place your grid on the needle."})
            
            # Update state
            tem_manual_state = "prepared"
            return True
            
        except Exception as e:
            print(f"Error during manual preparation: {e}")
            socketio.emit('function_response', {'result': f"Error during manual preparation: {e}"})
            return False
    
    # Use handle_robot_operation directly without control panel check
    return handle_robot_operation(_prepare_operation, robot=global_robot)

def tem_manual_expose(voltage, c_height, distance, time, process_run_id=None, motor1_enabled=False, motor2_enabled=False):
    """
    Consolidated manual TEM exposure with database logging and vibration motor support.
    This replaces both tem_manual_expose and enhanced_tem_manual_expose functions.
    
    This function:
    1. Moves the grid to the charger
    2. Turns on vibration motors if enabled
    3. Exposes the grid
    4. Turns off vibration motors
    5. Returns the grid to the manual position
    
    Args:
        voltage: Exposure voltage
        c_height: Container height
        distance: Vertical shift
        time: Exposure time
        process_run_id: Optional process run ID for database logging
        motor1_enabled: Enable vibration motor 1
        motor2_enabled: Enable vibration motor 2
    
    Returns:
        Success or error message
    """
    global tem_manual_state
    
    # State validation - can only run this if prepared
    if tem_manual_state != "prepared":
        message = f"Invalid operation: Please prepare the system first (Step 1), current state: {tem_manual_state}"
        print(message)
        socketio.emit('function_response', {'result': message})
        
        # Log the error if we have a process_run_id
        if process_run_id:
            log_error(process_run_id, message, "user")
        
        return message
    
    def _expose_operation(robot, voltage, c_height, distance, etime, motor1_enabled, motor2_enabled):
        try:
            global tem_manual_state
            
            # Format voltage and time to 5 characters with leading zeros
            voltage_formatted = f"{int(voltage):05d}"
            etime_formatted = f"{int(etime):05d}"
            
            print(f"Manual TEM exposure requested. Values: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}")
            if motor1_enabled or motor2_enabled:
                print(f"Vibration motors: Motor1={motor1_enabled}, Motor2={motor2_enabled}")
            socketio.emit('function_response', {'result': f"Manual TEM exposure requested with parameters: voltage={voltage}, c_height={c_height}, distance={distance}, time={etime}"})
            
            # Homing Z first
            robot.speed = SPEED_NORMAL
            robot.moveto(*robot.intermediate_pos["ZHOME"])
            socketio.emit('function_response', {'result': "Moving to charger..."})
            
            # Move to charger
            robot.moveto(*robot.equipment_pos["CHARGER_TEM"])
            
            # Position at calculated Z-height
            charger_z = MEASURED_BASE_HEIGHT - int(c_height)
            socketio.emit('function_response', {'result': f"Setting at: {charger_z} mm."})
            robot.moveto(z=charger_z)
            
            # Position at exposure height
            exposure_z = charger_z + int(distance)
            socketio.emit('function_response', {'result': f"Exposing at: {exposure_z} mm."})
            robot.moveto(z=exposure_z)
            
            # VIBRATION MOTOR INTEGRATION - Turn on motors before exposure
            if motor1_enabled or motor2_enabled:
                socketio.emit('function_response', {'result': "Turning on vibration motors..."})
                control_vibration_motors(motor1_enabled, motor2_enabled, turn_on=True)
                time.sleep(0.5)  # Brief delay to ensure motors are running
            
            # Perform exposure
            print(f"Grid will be exposed to {voltage} kV for {time} ms.")
            socketio.emit('function_response', {'result': f"Exposing grid to {voltage} kV for {time} ms..."})
            control_panel_hvps_setting(voltage_formatted, etime_formatted)
            
            # Wait for exposure to complete
            time.sleep(int(etime)/1000+2)
            socketio.emit('function_response', {'result': "Exposure complete"})
            
            # VIBRATION MOTOR INTEGRATION - Turn off motors after exposure
            if motor1_enabled or motor2_enabled:
                socketio.emit('function_response', {'result': "Turning off vibration motors..."})
                control_vibration_motors(motor1_enabled, motor2_enabled, turn_on=False)
            
            # Return to manual position
            robot.moveto(*robot.intermediate_pos["ZHOME"])
            socketio.emit('function_response', {'result': "Moving back to manual position..."})
            robot.moveto(*robot.equipment_pos["TEM_GRID_MANUAL_MODE"])
            
            print("Robot returned to manual position for grid removal")
            socketio.emit('function_response', {'result': "STEP 2 COMPLETE: Exposure finished. Robot returned to manual position. Please carefully remove your grid from the needle."})
            
            # State remains "prepared" to allow multiple exposures
            return True
            
        except Exception as e:
            error_msg = f"Error during manual exposure: {e}"
            print(error_msg)
            socketio.emit('function_response', {'result': error_msg})
            
            # Ensure motors are turned off in case of error
            try:
                if motor1_enabled or motor2_enabled:
                    control_panel_vibration_motor_all_off()
            except:
                pass
            
            # Log the error
            if process_run_id:
                log_error(process_run_id, error_msg, "process")
            else:
                log_standalone_error(error_msg, "process")
            
            return False
    
    try:
        # Use handle_robot_operation directly without control panel check
        result = handle_robot_operation(
            _expose_operation,
            robot=global_robot,
            voltage=voltage,
            c_height=c_height,
            distance=distance,
            etime=etime,
            motor1_enabled=motor1_enabled,
            motor2_enabled=motor2_enabled
        )
        return result
    except Exception as e:
        error_msg = f"TEM manual expose failed: {str(e)}"
        
        # Ensure motors are turned off in case of error
        try:
            if motor1_enabled or motor2_enabled:
                control_panel_vibration_motor_all_off()
        except:
            pass
        
        # Log the error
        if process_run_id:
            log_error(process_run_id, error_msg, "process")
        else:
            log_standalone_error(error_msg, "process")
        
        return error_msg

def tem_manual_prepare(process_run_id=None):
    """
    Consolidated manual TEM preparation with database logging.
    This replaces both tem_manual_prepare and enhanced_tem_manual_prepare functions.
    
    This function:
    1. Gets the robot ready
    2. Turns on the TEM vacuum pump
    3. Positions the head at TEM_GRID_MANUAL_MODE position
    
    Args:
        process_run_id: Optional process run ID for database logging
    
    Returns:
        Success or error message
    """
    global tem_manual_state
    
    # State validation - can only run this if idle
    if tem_manual_state != "idle":
        message = f"Invalid operation: System must be in idle state to prepare, current state: {tem_manual_state}"
        print(message)
        socketio.emit('function_response', {'result': message})
        
        # Log the error if we have a process_run_id
        if process_run_id:
            log_error(process_run_id, message, "user")
        
        return message
    
    def _prepare_operation(robot):
        try:
            global tem_manual_state
            
            print("Manual TEM preparation requested")
            socketio.emit('function_response', {'result': "Manual TEM preparation requested"})
            
            # Home the robot first
            try:
                robot.gohome()
            except Exception as var_error:
                error_msg = f"An error occurred during homing: {var_error}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg})
                
                # Log the homing error
                if process_run_id:
                    log_error(process_run_id, error_msg, "robot")
                else:
                    log_standalone_error(error_msg, "robot")
                
                return False
            
            # Turn on the TEM vacuum pump
            control_panel_vacuum("TEM", True)
            socketio.emit('function_response', {'result': "TEM vacuum pump activated"})
            
            # Position at manual mode position
            robot.speed = SPEED_NORMAL
            robot.moveto(*robot.equipment_pos["TEM_GRID_MANUAL_MODE"])
            robot.moveto(*robot.equipment_pos["TEM_GRID_MANUAL_MODE_Z"])
            socketio.emit('function_response', {'result': "Robot positioned for manual grid placement"})
            
            print("System ready for manual grid placement")
            socketio.emit('function_response', {'result': "STEP 1 COMPLETE: System ready for manual grid placement. Please carefully place your grid on the needle."})
            
            # Update state
            tem_manual_state = "prepared"
            return True
            
        except Exception as e:
            error_msg = f"Error during manual preparation: {e}"
            print(error_msg)
            socketio.emit('function_response', {'result': error_msg})
            
            # Log the error
            if process_run_id:
                log_error(process_run_id, error_msg, "process")
            else:
                log_standalone_error(error_msg, "process")
            
            return False
    
    try:
        # Use handle_robot_operation directly without control panel check
        result = handle_robot_operation(_prepare_operation, robot=global_robot)
        return result
    except Exception as e:
        error_msg = f"TEM manual prepare failed: {str(e)}"
        
        # Log the error
        if process_run_id:
            log_error(process_run_id, error_msg, "process")
        else:
            log_standalone_error(error_msg, "process")
        
        return error_msg

def tem_manual_complete(process_run_id=None):
    """
    Consolidated manual TEM completion with database logging.
    This replaces both tem_manual_complete and enhanced_tem_manual_complete functions.
    
    This function:
    1. Turns off the TEM vacuum pump
    2. Returns robot to home position
    
    Args:
        process_run_id: Optional process run ID for database logging
    
    Returns:
        Success or error message
    """
    global tem_manual_state
    
    # State validation - can run this if prepared or exposed (allowing abort after step 1)
    if tem_manual_state != "prepared" and tem_manual_state != "exposed":
        message = f"Invalid operation: Please prepare the system first (Step 1), current state: {tem_manual_state}"
        print(message)
        socketio.emit('function_response', {'result': message})
        
        # Log the error if we have a process_run_id
        if process_run_id:
            log_error(process_run_id, message, "user")
        
        return message
    
    def _complete_operation(robot):
        try:
            global tem_manual_state
            
            print("Completing manual TEM procedure")
            socketio.emit('function_response', {'result': "Completing manual TEM procedure..."})
            
            # Turn off vacuum pump
            control_panel_vacuum("TEM", False)
            control_panel_standby()
            socketio.emit('function_response', {'result': "Vacuum pump turned off"})
            
            # Return to home positions
            robot.moveto(*robot.intermediate_pos["ZHOME"])
            socketio.emit('function_response', {'result': "Moving to home position..."})
            robot.moveto(*robot.intermediate_pos["HOME"])
            
            print("Manual TEM procedure completed successfully")
            socketio.emit('function_response', {'result': "PROCEDURE COMPLETE: System has been reset and is ready for next operation."})
            
            # Reset state
            tem_manual_state = "idle"
            return True
            
        except Exception as e:
            error_msg = f"Error completing manual procedure: {e}"
            print(error_msg)
            socketio.emit('function_response', {'result': error_msg})
            
            # Log the error
            if process_run_id:
                log_error(process_run_id, error_msg, "process")
            else:
                log_standalone_error(error_msg, "process")
            
            return False
    
    try:
        # Use handle_robot_operation directly without control panel check
        result = handle_robot_operation(_complete_operation, robot=global_robot)
        return result
    except Exception as e:
        error_msg = f"TEM manual complete failed: {str(e)}"
        
        # Log the error
        if process_run_id:
            log_error(process_run_id, error_msg, "process")
        else:
            log_standalone_error(error_msg, "process")
        
        return error_msg

def device_extend_bed(process_run_id=None):
    """
    Consolidated bed extension with database logging.
    This replaces both device_extend_bed and enhanced_device_extend_bed functions.
    
    Args:
        process_run_id: Optional process run ID for database logging
    
    Returns:
        Success or error message
    """
    try:
        result = device_move_bed("extend")
        
        # Log if there's an error
        if isinstance(result, str) and ("error" in result.lower() or "failed" in result.lower()):
            if process_run_id:
                log_error(process_run_id, f"Bed extension failed: {result}", "robot")
            else:
                log_standalone_error(f"Bed extension failed: {result}", "robot")
        
        return result
    except Exception as e:
        error_msg = f"Bed extension error: {str(e)}"
        
        # Log the error
        if process_run_id:
            log_error(process_run_id, error_msg, "robot")
        else:
            log_standalone_error(error_msg, "robot")
        
        return error_msg

def device_retract_bed(process_run_id=None):
    """
    Consolidated bed retraction with database logging.
    This replaces both device_retract_bed and enhanced_device_retract_bed functions.
    
    Args:
        process_run_id: Optional process run ID for database logging
    
    Returns:
        Success or error message
    """
    try:
        result = device_move_bed("retract")
        
        # Log if there's an error
        if isinstance(result, str) and ("error" in result.lower() or "failed" in result.lower()):
            if process_run_id:
                log_error(process_run_id, f"Bed retraction failed: {result}", "robot")
            else:
                log_standalone_error(f"Bed retraction failed: {result}", "robot")
        
        return result
    except Exception as e:
        error_msg = f"Bed retraction error: {str(e)}"
        
        # Log the error
        if process_run_id:
            log_error(process_run_id, error_msg, "robot")
        else:
            log_standalone_error(error_msg, "robot")
        
        return error_msg

def control_panel_get_macstat(process_run_id=None):
    """
    Consolidated MACSTAT command with database logging.
    This replaces both control_panel_get_macstat and enhanced_control_panel_get_macstat functions.
    
    Args:
        process_run_id: Optional process run ID for database logging
    
    Returns:
        PLC response string or error message
    """
    try:
        result = send_plc_command("MACSTAT")
        
        # Log if we get an unexpected response
        if isinstance(result, str) and ("error" in result.lower() or "timeout" in result.lower()):
            error_msg = f"MACSTAT command failed: {result}"
            if process_run_id:
                log_error(process_run_id, error_msg, "PLC")
            else:
                log_standalone_error(error_msg, "PLC")
        
        return result
        
    except Exception as e:
        error_msg = f"MACSTAT command error: {str(e)}"
        
        # Log the error
        if process_run_id:
            log_error(process_run_id, error_msg, "PLC")
        else:
            log_standalone_error(error_msg, "PLC")
        
        return error_msg
    
#region - State check and Position availability functions

# Global variable to track current operation
current_remote_operation = None

def state_check():
    """
    Simplified state check that always returns the last operation result.
    
    Returns: JSON string with comprehensive state information
    """
    try:
        # Check if any process is currently running
        global current_process_runs, current_remote_operation
        
        if current_process_runs or current_remote_operation:
            # System is running an operation
            operation_info = {}
            if current_remote_operation:
                operation_info['current_operation'] = current_remote_operation
            if current_process_runs:
                operation_info['active_processes'] = list(current_process_runs.values())
            
            result = {
                'status': 'running',
                'details': operation_info,
                'timestamp': datetime.now().isoformat()
            }
        else:
            # System is idle - get the last process result
            result = {
                'status': 'idle',
                'timestamp': datetime.now().isoformat()
            }
            
            # Always get the last process result
            last_process = get_last_process_result()
            
            if last_process:
                # Always include last operation info
                result.update({
                    'last_operation': last_process['process_type'],
                    'last_operation_result': 'success' if last_process['success'] else 'failed',
                    'last_operation_timestamp': last_process['timestamp']
                })
                
                # Add details based on success/failure
                if last_process['success']:
                    result['last_operation_details'] = f"{last_process['process_type']} completed successfully"
                    if last_process['duration_seconds']:
                        result['duration_seconds'] = last_process['duration_seconds']
                else:
                    # Include error details for failures
                    result['last_operation_details'] = last_process['error_message'] or f"{last_process['process_type']} failed"
                    if last_process['error_category']:
                        result['error_category'] = last_process['error_category']
                    if last_process['component']:
                        result['error_component'] = last_process['component']
                
                # Always include parameters if available
                if last_process['parameters']:
                    try:
                        result['last_operation_parameters'] = json.loads(last_process['parameters'])
                    except:
                        pass
            else:
                # No previous operations found
                result.update({
                    'last_operation': 'none',
                    'last_operation_result': 'none',
                    'last_operation_details': 'No previous operations recorded'
                })
        
        return json.dumps(result)
        
    except Exception as e:
        error_result = {
            'status': 'error',
            'error_message': f"State check failed: {str(e)}",
            'timestamp': datetime.now().isoformat()
        }
        return json.dumps(error_result)

def get_sem_position_status():
    """Get SEM position status for remote monitoring."""
    try:
        positions = get_sem_positions()
        result = {
            'type': 'sem_positions',
            'positions': positions,
            'timestamp': datetime.now().isoformat()
        }
        return json.dumps(result)
    except Exception as e:
        error_result = {
            'type': 'error',
            'message': f"Failed to get SEM positions: {str(e)}",
            'timestamp': datetime.now().isoformat()
        }
        return json.dumps(error_result)

def get_tem_position_status():
    """Get TEM position status for remote monitoring."""
    try:
        positions = get_tem_positions()
        result = {
            'type': 'tem_positions',
            'positions': positions,
            'timestamp': datetime.now().isoformat()
        }
        return json.dumps(result)
    except Exception as e:
        error_result = {
            'type': 'error',
            'message': f"Failed to get TEM positions: {str(e)}",
            'timestamp': datetime.now().isoformat()
        }
        return json.dumps(error_result)

def clear_sem_memory():
    """Clear SEM position memory."""
    try:
        if clear_sem_positions():
            return "SUCCESS: SEM position memory cleared"
        else:
            return "ERROR: Failed to clear SEM position memory"
    except Exception as e:
        return f"ERROR: {str(e)}"

def clear_tem_memory():
    """Clear TEM position memory."""
    try:
        if clear_tem_positions():
            return "SUCCESS: TEM position memory cleared"
        else:
            return "ERROR: Failed to clear TEM position memory"
    except Exception as e:
        return f"ERROR: {str(e)}"

#function created to update the color on the SVG file
def get_sem_position_status():
    """Get SEM position statuses formatted for frontend display."""
    try:
        positions = get_sem_positions()  # This function already exists in database.py
        
        if positions:
            # Return as JSON string for the frontend
            import json
            return json.dumps(positions)
        else:
            return "ERROR: Could not retrieve SEM position data"
            
    except Exception as e:
        return f"ERROR: {str(e)}"

#function created to update the color on the SVG file
def get_tem_position_status():
    """Get TEM position statuses formatted for frontend display."""
    try:
        positions = get_tem_positions()  # This function already exists in database.py
        
        if positions:
            # Return as JSON string for the frontend
            import json
            return json.dumps(positions)
        else:
            return "ERROR: Could not retrieve TEM position data"
            
    except Exception as e:
        return f"ERROR: {str(e)}"

#endregion

#region - SEM pick and place soak test functions

def soak_test_sem_pick_place(session_id, config):
    """
    Main SEM Pick & Place soak test function.
    This runs in a separate thread to avoid blocking the web interface.
    
    Args:
        session_id (int): Database session ID
        config (dict): Test configuration
    """
    global soak_test_in_progress, current_soak_test_session
    
    # Define test positions in the correct sequence
    test_positions = ['A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'C1', 'C2', 'C3', 
                     'D1', 'D2', 'D3', 'E1', 'E2', 'E3']
    
    target_cycles = config.get('cycles', 1)
    max_retries = config.get('max_retries', 3)
    failure_handling = config.get('failure_handling', 'skip')
    recovery_mode = config.get('recovery_mode', 'continue')
    
    try:
        print(f"Starting SEM Pick & Place soak test - Session {session_id}")
        
        # Initialize robot
        robot_ready = False
        retry_count = 0
        max_robot_retries = 3
        
        while not robot_ready and retry_count < max_robot_retries:
            if soak_test_stop_event.is_set():
                raise Exception("Test stopped by user during initialization")
                
            try:
                # Initialize robot using existing function
                success, result = c3dp_test_connectivity(complete_test=False)
                if success:
                    robot_ready = True
                    log_soak_test_operation(
                        session_id, 'robot_init', None, True, 
                        operation_data={'result': result}
                    )
                else:
                    raise Exception(f"Robot initialization failed: {result}")
                    
            except Exception as e:
                retry_count += 1
                error_msg = f"Robot initialization attempt {retry_count} failed: {str(e)}"
                print(error_msg)
                
                log_soak_test_error(
                    session_id, 'Robot_Communication', error_msg,
                    component='robot', recovery_action=f"Retry {retry_count}/{max_robot_retries}"
                )
                
                if retry_count >= max_robot_retries:
                    raise Exception(f"Failed to initialize robot after {max_robot_retries} attempts")
                
                time.sleep(5)  # Wait before retry
        
        # Home the robot
        if not soak_test_stop_event.is_set():
            try:
                device_step_zero()
                log_soak_test_operation(session_id, 'robot_home', None, True)
            except Exception as e:
                error_msg = f"Robot homing failed: {str(e)}"
                log_soak_test_error(session_id, 'Robot_Movement', error_msg, component='robot')
                raise Exception(error_msg)
        
        # Main test loop
        total_operations = 0
        successful_operations = 0
        failed_operations = 0
        
        for cycle in range(1, target_cycles + 1):
            if soak_test_stop_event.is_set():
                break
                
            print(f"Starting cycle {cycle}/{target_cycles}")
            
            # Create cycle record
            cycle_id = create_soak_test_cycle(session_id, cycle)
            cycle_start_time = time.time()
            
            # Update session status
            update_soak_test_session(session_id, {
                'current_cycle': cycle,
                'current_step': f'Starting cycle {cycle}'
            })
            
            cycle_successful = 0
            cycle_failed = 0
            
            # Test each position in the cycle
            for position in test_positions:
                if soak_test_stop_event.is_set():
                    break
                    
                print(f"Testing position {position} (Cycle {cycle})")
                
                # Update current position
                update_soak_test_session(session_id, {
                    'current_position': position,
                    'current_step': f'Testing position {position}'
                })
                
                # Perform pick and place operation
                position_success = False
                retry_attempts = 0
                
                while not position_success and retry_attempts <= max_retries:
                    if soak_test_stop_event.is_set():
                        break
                        
                    operation_start_time = time.time()
                    
                    try:
                        # Call the individual position test
                        position_success = test_single_position_sem(
                            position, session_id, cycle_id, retry_attempts
                        )
                        
                        operation_duration = time.time() - operation_start_time
                        total_operations += 1
                        
                        if position_success:
                            successful_operations += 1
                            cycle_successful += 1
                            log_soak_test_operation(
                                session_id, 'position_test', position, True,
                                cycle_id=cycle_id, retry_count=retry_attempts,
                                duration_seconds=operation_duration
                            )
                            print(f"Position {position} - SUCCESS")
                        else:
                            if failure_handling == 'stop':
                                raise Exception(f"Test stopped due to failure at position {position}")
                            elif failure_handling == 'skip':
                                failed_operations += 1
                                cycle_failed += 1
                                break  # Skip to next position
                            elif failure_handling == 'retry':
                                retry_attempts += 1
                                if retry_attempts > max_retries:
                                    failed_operations += 1
                                    cycle_failed += 1
                                    break
                                else:
                                    print(f"Position {position} - RETRY {retry_attempts}")
                                    continue
                        
                    except Exception as e:
                        operation_duration = time.time() - operation_start_time
                        error_msg = f"Position {position} test failed: {str(e)}"
                        print(error_msg)
                        
                        log_soak_test_error(
                            session_id, determine_soak_error_category(str(e), 'position_test'),
                            error_msg, position=position, cycle_number=cycle, component='robot'
                        )
                        
                        log_soak_test_operation(
                            session_id, 'position_test', position, False,
                            cycle_id=cycle_id, retry_count=retry_attempts,
                            error_message=error_msg, duration_seconds=operation_duration
                        )
                        
                        if failure_handling == 'stop':
                            raise Exception(f"Test stopped due to error at position {position}: {str(e)}")
                        elif failure_handling == 'skip':
                            failed_operations += 1
                            cycle_failed += 1
                            break
                        elif failure_handling == 'retry':
                            retry_attempts += 1
                            if retry_attempts > max_retries:
                                failed_operations += 1
                                cycle_failed += 1
                                break
                
                # Update statistics after each position
                current_success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0
                update_soak_test_session(session_id, {
                    'total_operations': total_operations,
                    'successful_operations': successful_operations,
                    'failed_operations': failed_operations,
                    'current_success_rate': current_success_rate
                })
            
            # Complete cycle
            cycle_duration = time.time() - cycle_start_time
            cycle_success_rate = (cycle_successful / len(test_positions) * 100) if len(test_positions) > 0 else 0
            
            # Update cycle record
            if cycle_id:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE soak_test_cycles 
                        SET end_time = ?, status = ?, total_positions = ?, 
                            successful_positions = ?, failed_positions = ?, success_rate = ?
                        WHERE id = ?
                    """, (
                        datetime.now(), 'completed', len(test_positions),
                        cycle_successful, cycle_failed, cycle_success_rate, cycle_id
                    ))
                    conn.commit()
            
            print(f"Cycle {cycle} completed - Success rate: {cycle_success_rate:.1f}%")
            
            # Update session
            update_soak_test_session(session_id, {
                'completed_cycles': cycle,
                'current_step': f'Completed cycle {cycle}'
            })
        
        # Test completed successfully
        final_success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0
        session_data = get_soak_test_session(session_id)
        start_time = datetime.fromisoformat(session_data['start_time'])
        total_duration = (datetime.now() - start_time).total_seconds() / 60
        
        update_soak_test_session(session_id, {
            'status': 'completed',
            'end_time': datetime.now(),
            'final_success_rate': final_success_rate,
            'duration_minutes': total_duration,
            'current_step': 'Test completed successfully'
        })
        
        print(f"SEM Pick & Place test completed successfully - Final success rate: {final_success_rate:.1f}%")
        
    except Exception as e:
        # Test failed
        error_msg = f"SEM Pick & Place test failed: {str(e)}"
        print(error_msg)
        
        update_soak_test_session(session_id, {
            'status': 'failed',
            'end_time': datetime.now(),
            'failure_reason': str(e),
            'current_step': 'Test failed'
        })
        
        log_soak_test_error(
            session_id, determine_soak_error_category(str(e), 'test_execution'),
            error_msg, component='system'
        )
    
    finally:
        # Cleanup
        print("Cleaning up SEM Pick & Place test")
        
        try:
            # Return robot to home position
            device_step_final()
        except Exception as e:
            print(f"Error during cleanup: {e}")
        
        # Reset global flags
        soak_test_in_progress = False
        current_soak_test_session = None
        soak_test_stop_event.clear()

def test_single_position_sem(position, session_id, cycle_id, retry_count):
    """
    Test a single SEM stub position (pick, detect, return).
    
    Args:
        position (str): Position to test (e.g., 'A1')
        session_id (int): Database session ID
        cycle_id (int): Database cycle ID
        retry_count (int): Current retry attempt
    
    Returns:
        bool: True if successful, False if failed
    """
    global global_robot
    
    try:
        if not global_robot:
            raise Exception("Robot not initialized")
        
        print(f"Testing position {position} (attempt {retry_count + 1})")
        
        # Step 1: Move to standby position
        global_robot.speed = SPEED_NORMAL
        global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
        
        # Step 2: Turn on vacuum
        control_panel_vacuum("SEM", True)
        time.sleep(0.5)  # Brief pause for vacuum to stabilize
        
        # Step 3: Move to position and attempt to pick stub
        print(f"Moving to position {position} for stub pickup")
        global_robot.moveto(*global_robot.clean_stub_pos[position])
        
        # Descend to pickup position with progressive speeds
        global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z1"])  # First descent level
        global_robot.speed = SPEED_LOW
        global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z2"])  # Second descent level
        global_robot.speed = SPEED_VLOW
        global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z3"])  # Final pickup position
        
        # Brief contact for pickup
        time.sleep(0.2)
        
        # Ascend from pickup position
        global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z2"])
        global_robot.speed = SPEED_NORMAL
        global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
        
        # Step 4: Move to laser detection position
        print(f"Moving to laser for detection verification")
        global_robot.moveto(*global_robot.equipment_pos["LASER_SEM"])
        global_robot.moveto(*global_robot.equipment_pos["LASER_SEM_Z1"])
        
        # Step 5: Check laser detection
        laser_result = control_panel_laser_status()
        print(f"Laser detection result: {laser_result}")
        
        if laser_result == "LASER1":
            # Stub successfully detected - now return it to original position
            print(f"Stub detected! Returning to position {position}")
            
            # Move back to standby
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Return to original position
            global_robot.moveto(*global_robot.clean_stub_pos[position])
            global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z1"])
            global_robot.speed = SPEED_LOW
            global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z2"])
            global_robot.speed = SPEED_VLOW
            global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z3"])
            
            # Turn off vacuum to release stub
            control_panel_vacuum("SEM", False)
            time.sleep(PAUSE_VAC)  # Wait for vacuum release
            
            # Ascend after placing stub
            global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z2"])
            global_robot.speed = SPEED_NORMAL
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Log successful operation
            log_soak_test_operation(
                session_id, 'laser_detection', position, True,
                cycle_id=cycle_id, retry_count=retry_count,
                operation_data={'laser_result': laser_result, 'action': 'stub_returned'}
            )
            
            print(f"Position {position} test completed successfully")
            return True
            
        else:
            # Stub not detected - this is a failure
            print(f"Laser detection failed for position {position}")
            
            # Move back to standby and turn off vacuum
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            control_panel_vacuum("SEM", False)
            
            # Log the detection failure
            error_msg = f"Laser detection failed for position {position} - Expected LASER1, got {laser_result}"
            
            log_soak_test_error(
                session_id, 'Laser_Detection', error_msg,
                position=position, component='laser',
                recovery_action=f'retry_{retry_count + 1}' if retry_count < 3 else 'skip'
            )
            
            log_soak_test_operation(
                session_id, 'laser_detection', position, False,
                cycle_id=cycle_id, retry_count=retry_count,
                error_message=error_msg,
                operation_data={'laser_result': laser_result, 'expected': 'LASER1'}
            )
            
            print(f"Position {position} test failed - no stub detected")
            return False
            
    except Exception as e:
        # Handle any errors during the test
        error_msg = f"Position {position} test failed with exception: {str(e)}"
        print(error_msg)
        
        # Ensure cleanup in case of error
        try:
            # Turn off vacuum and return to safe position
            control_panel_vacuum("SEM", False)
            if global_robot:
                global_robot.speed = SPEED_NORMAL
                global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
        except Exception as cleanup_error:
            print(f"Error during cleanup: {cleanup_error}")
        
        # Log the error
        log_soak_test_error(
            session_id, determine_soak_error_category(str(e), 'position_test'),
            error_msg, position=position, component='robot',
            recovery_action=f'retry_{retry_count + 1}' if retry_count < 3 else 'abort'
        )
        
        # Don't raise the exception - return False to indicate failure
        # The calling function will handle retry logic
        return False

#endregion

#region - SEM to Stage soak test functions

def soak_test_sem_to_stage(session_id, config):
    """
    Main SEM to Stage Transfer soak test function.
    Tests one-way transfer from SEM tray to stage positions.
    
    Args:
        session_id (int): Database session ID
        config (dict): Test configuration
    """
    global soak_test_in_progress, current_soak_test_session
    
    # Define test sequences
    tray_positions = ['A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'C1', 'C2', 'C3', 
                     'D1', 'D2', 'D3', 'E1', 'E2', 'E3']
    
    stage_positions = ['PH_STUB_A', 'PH_STUB_B', 'PH_STUB_C', 'PH_STUB_8', 'PH_STUB_10', 
                      'PH_STUB_D', 'PH_STUB_F', 'PH_STUB_15', 'PH_STUB_17', 'PH_STUB_G', 
                      'PH_STUB_I', 'PH_STUB_K', 'PH_STUB_20', 'PH_STUB_22', 'PH_STUB_L', 
                      'PH_STUB_N', 'PH_STUB_27', 'PH_STUB_29', 'PH_STUB_O', 'PH_STUB_P', 
                      'PH_STUB_Q']
    
    # Create transfer pairs (tray → stage)
    transfer_pairs = []
    for i, tray_pos in enumerate(tray_positions):
        if i < len(stage_positions):
            transfer_pairs.append((tray_pos, stage_positions[i]))
    
    max_retries = config.get('max_retries', 3)
    failure_handling = config.get('failure_handling', 'skip')
    
    try:
        print(f"Starting SEM to Stage Transfer test - Session {session_id}")
        
        # Initialize robot
        robot_ready = False
        retry_count = 0
        max_robot_retries = 3
        
        while not robot_ready and retry_count < max_robot_retries:
            if soak_test_stop_event.is_set():
                raise Exception("Test stopped by user during initialization")
                
            try:
                success, result = c3dp_test_connectivity(complete_test=False)
                if success:
                    robot_ready = True
                    log_soak_test_operation(
                        session_id, 'robot_init', None, True, 
                        operation_data={'result': result}
                    )
                else:
                    raise Exception(f"Robot initialization failed: {result}")
                    
            except Exception as e:
                retry_count += 1
                error_msg = f"Robot initialization attempt {retry_count} failed: {str(e)}"
                print(error_msg)
                
                log_soak_test_error(
                    session_id, 'Robot_Communication', error_msg,
                    component='robot', recovery_action=f"Retry {retry_count}/{max_robot_retries}"
                )
                
                if retry_count >= max_robot_retries:
                    raise Exception(f"Failed to initialize robot after {max_robot_retries} attempts")
                
                time.sleep(5)
        
        # Home the robot
        if not soak_test_stop_event.is_set():
            try:
                device_step_zero()
                log_soak_test_operation(session_id, 'robot_home', None, True)
            except Exception as e:
                error_msg = f"Robot homing failed: {str(e)}"
                log_soak_test_error(session_id, 'Robot_Movement', error_msg, component='robot')
                raise Exception(error_msg)
        
        # Main test loop - single run through all transfers
        total_operations = 0
        successful_operations = 0
        failed_operations = 0
        
        # Create single cycle record
        cycle_id = create_soak_test_cycle(session_id, 1)
        cycle_start_time = time.time()
        
        # Update session status
        update_soak_test_session(session_id, {
            'current_cycle': 1,
            'target_cycles': 1,
            'current_step': 'Starting transfers'
        })
        
        # Process each transfer pair
        for tray_pos, stage_pos in transfer_pairs:
            if soak_test_stop_event.is_set():
                break
                
            print(f"Testing transfer {tray_pos} → {stage_pos}")
            
            # Update current position
            update_soak_test_session(session_id, {
                'current_position': f"{tray_pos} → {stage_pos}",
                'current_step': f'Transferring {tray_pos} → {stage_pos}'
            })
            
            # Perform transfer operation
            transfer_success = perform_sem_to_stage_transfer(
                session_id, cycle_id, tray_pos, stage_pos, 
                max_retries, failure_handling
            )
            
            total_operations += 1
            
            if transfer_success:
                successful_operations += 1
                print(f"Transfer {tray_pos} → {stage_pos} - SUCCESS")
            else:
                failed_operations += 1
                print(f"Transfer {tray_pos} → {stage_pos} - FAILED")
                
                if failure_handling == 'stop':
                    raise Exception(f"Test stopped due to failure at {tray_pos} → {stage_pos}")
            
            # Update statistics after each transfer
            current_success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0
            update_soak_test_session(session_id, {
                'total_operations': total_operations,
                'successful_operations': successful_operations,
                'failed_operations': failed_operations,
                'current_success_rate': current_success_rate
            })
        
        # Complete the test
        cycle_duration = time.time() - cycle_start_time
        cycle_success_rate = (successful_operations / len(transfer_pairs) * 100) if len(transfer_pairs) > 0 else 0
        
        # Update cycle record
        if cycle_id:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE soak_test_cycles 
                    SET end_time = ?, status = ?, total_positions = ?, 
                        successful_positions = ?, failed_positions = ?, success_rate = ?
                    WHERE id = ?
                """, (
                    datetime.now(), 'completed', len(transfer_pairs),
                    successful_operations, failed_operations, cycle_success_rate, cycle_id
                ))
                conn.commit()
        
        # Test completed successfully
        session_data = get_soak_test_session(session_id)
        start_time = datetime.fromisoformat(session_data['start_time'])
        total_duration = (datetime.now() - start_time).total_seconds() / 60
        
        update_soak_test_session(session_id, {
            'status': 'completed',
            'end_time': datetime.now(),
            'completed_cycles': 1,
            'final_success_rate': cycle_success_rate,
            'duration_minutes': total_duration,
            'current_step': 'Test completed successfully'
        })
        
        print(f"SEM to Stage Transfer test completed successfully - Final success rate: {cycle_success_rate:.1f}%")
        
    except Exception as e:
        # Test failed
        error_msg = f"SEM to Stage Transfer test failed: {str(e)}"
        print(error_msg)
        
        update_soak_test_session(session_id, {
            'status': 'failed',
            'end_time': datetime.now(),
            'failure_reason': str(e),
            'current_step': 'Test failed'
        })
        
        log_soak_test_error(
            session_id, determine_soak_error_category(str(e), 'test_execution'),
            error_msg, component='system'
        )
    
    finally:
        # Cleanup
        print("Cleaning up SEM to Stage Transfer test")
        
        try:
            # Return robot to home position
            device_step_final()
        except Exception as e:
            print(f"Error during cleanup: {e}")
        
        # Reset global flags
        soak_test_in_progress = False
        current_soak_test_session = None
        soak_test_stop_event.clear()

def perform_sem_to_stage_transfer(session_id, cycle_id, tray_pos, stage_pos, max_retries, failure_handling):
    """
    Perform a single SEM stub transfer from tray to stage.
    
    Args:
        session_id (int): Database session ID
        cycle_id (int): Database cycle ID
        tray_pos (str): Tray position (A1, A2, etc.)
        stage_pos (str): Stage position (PH_STUB_A, etc.)
        max_retries (int): Maximum retry attempts
        failure_handling (str): Failure handling strategy
    
    Returns:
        bool: True if successful, False if failed
    """
    global global_robot
    
    retry_count = 0
    
    while retry_count <= max_retries:
        try:
            if not global_robot:
                raise Exception("Robot not initialized")
            
            print(f"Transferring stub {tray_pos}→{stage_pos} (attempt {retry_count + 1})")
            
            operation_start_time = time.time()
            
            # Step 1: Move to standby position
            global_robot.speed = SPEED_NORMAL
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Step 2: Turn on SEM vacuum
            control_panel_vacuum("SEM", True)
            
            # Step 3: Pick up stub from tray
            print(f"Picking stub from {tray_pos}")
            global_robot.moveto(*global_robot.clean_stub_pos[tray_pos])
            
            # Descending needle with progressive speeds
            global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z1"])
            global_robot.speed = SPEED_LOW
            global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z2"])
            global_robot.speed = SPEED_VLOW
            global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z3"])
            global_robot.moveto(*global_robot.clean_stub_pos["STRAY_Z2"])
            global_robot.speed = SPEED_NORMAL
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Step 4: Laser verification
            print(f"Checking laser detection for {tray_pos}")
            global_robot.moveto(*global_robot.equipment_pos["LASER_SEM"])
            global_robot.moveto(*global_robot.equipment_pos["LASER_SEM_Z1"])
            
            laser_result = control_panel_laser_status()
            if laser_result != "LASER1":
                print(f"Laser detection failed for {tray_pos}")
                log_soak_test_error(
                    session_id, 'Laser_Detection',
                    f"Stub not detected at {tray_pos} - Expected LASER1, got {laser_result}",
                    position=f"{tray_pos}→{stage_pos}", component='laser'
                )
                
                # Turn off vacuum and retry or fail
                global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
                control_panel_vacuum("SEM", False)
                
                if failure_handling == 'retry' and retry_count < max_retries:
                    retry_count += 1
                    print(f"Retrying {tray_pos}→{stage_pos} (attempt {retry_count + 1})")
                    continue
                else:
                    operation_duration = time.time() - operation_start_time
                    log_soak_test_operation(
                        session_id, 'tray_to_stage_transfer', f"{tray_pos}→{stage_pos}", False,
                        cycle_id=cycle_id, retry_count=retry_count,
                        error_message=f"Stub not picked from {tray_pos}",
                        duration_seconds=operation_duration
                    )
                    return False
            
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Step 5: Transfer to rotator
            print(f"Moving to rotator for orientation")
            control_panel_rotator("faceDown")  # Home rotator
            control_panel_gripper_home()      # Home gripper
            
            global_robot.moveto(*global_robot.equipment_pos["ROTATOR_0"])
            global_robot.moveto(*global_robot.equipment_pos["ROTATOR_Z1"])
            global_robot.speed = SPEED_VLOW
            global_robot.moveto(*global_robot.equipment_pos["ROTATOR_ENGAGE"])
            control_panel_vacuum("SEM", False)  # Release from needle
            time.sleep(PAUSE_VAC)
            global_robot.speed = SPEED_NORMAL
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Step 6: Rotate stub face up
            control_panel_rotator("faceUp")
            
            # Step 7: Pick up with gripper
            global_robot.moveto(*global_robot.equipment_pos["GRIPPER_ROTATOR_0"])
            global_robot.moveto(*global_robot.equipment_pos["GRIPPER_ROTATOR_Z1"])
            control_panel_gripper_close()  # Close gripper on stub
            global_robot.speed = SPEED_VLOW
            global_robot.moveto(*global_robot.equipment_pos["GRIPPER_ROTATOR_DISENGAGE"])
            global_robot.speed = SPEED_NORMAL
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Step 8: Reset rotator
            control_panel_rotator("faceDown")
            
            # Step 9: Move to stage and place stub
            print(f"Placing stub at {stage_pos}")
            
            # Get the lid position for this stage position
            stage_lid_value = int(global_robot.phenom_stub_pos[stage_pos][4])
            control_panel_sem_stage_partial_open(stage_lid_value)
            
            global_robot.moveto(*global_robot.phenom_stub_pos[stage_pos])
            global_robot.moveto(*global_robot.phenom_stub_pos["PH_Z1"])
            global_robot.speed = SPEED_LOW
            global_robot.moveto(*global_robot.phenom_stub_pos["PH_Z2"])
            global_robot.speed = SPEED_VLOW
            global_robot.moveto(*global_robot.phenom_stub_pos["PH_Z3"])
            
            # Release stub from gripper
            control_panel_gripper_release()
            global_robot.moveto(*global_robot.phenom_stub_pos["PH_Z4"])
            
            # Press stub down gently
            control_panel_gripper_press()
            global_robot.moveto(*global_robot.phenom_stub_pos["PH_Z5"])
            
            # Open gripper and withdraw
            control_panel_gripper_home()
            global_robot.speed = SPEED_NORMAL
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Step 10: Close stage lid
            control_panel_sem_stage_close()
            
            # Step 11: Return to home position
            global_robot.moveto(x=global_robot.intermediate_pos["HOME"][0])
            global_robot.moveto(y=global_robot.intermediate_pos["HOME"][1])
            
            # Log successful operation
            operation_duration = time.time() - operation_start_time
            log_soak_test_operation(
                session_id, 'tray_to_stage_transfer', f"{tray_pos}→{stage_pos}", True,
                cycle_id=cycle_id, retry_count=retry_count,
                duration_seconds=operation_duration,
                operation_data={'stage_lid_value': stage_lid_value}
            )
            
            print(f"Successfully transferred stub {tray_pos}→{stage_pos}")
            return True
            
        except Exception as e:
            # Handle any errors during the transfer
            error_msg = f"Transfer {tray_pos}→{stage_pos} failed: {str(e)}"
            print(error_msg)
            
            # Ensure cleanup in case of error
            try:
                control_panel_vacuum("SEM", False)
                control_panel_gripper_home()
                control_panel_rotator("faceDown")
                control_panel_sem_stage_close()
                if global_robot:
                    global_robot.speed = SPEED_NORMAL
                    global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            except Exception as cleanup_error:
                print(f"Error during cleanup: {cleanup_error}")
            
            # Log the error
            log_soak_test_error(
                session_id, determine_soak_error_category(str(e), 'tray_to_stage_transfer'),
                error_msg, position=f"{tray_pos}→{stage_pos}", component='robot'
            )
            
            # Decide whether to retry
            if failure_handling == 'retry' and retry_count < max_retries:
                retry_count += 1
                print(f"Retrying {tray_pos}→{stage_pos} (attempt {retry_count + 1})")
                time.sleep(2)  # Longer pause before retry for complex operation
                continue
            else:
                # Log failed operation
                operation_duration = time.time() - operation_start_time
                log_soak_test_operation(
                    session_id, 'tray_to_stage_transfer', f"{tray_pos}→{stage_pos}", False,
                    cycle_id=cycle_id, retry_count=retry_count,
                    error_message=error_msg, duration_seconds=operation_duration
                )
                return False
    
    # If we exit the while loop, all retries failed
    return False

#endregion

#region - Communication stress soak test functions

def soak_test_communication_stress(session_id, config):
    """
    Main Communication Stress soak test function.
    Tests PLC and/or robot communication at various speeds.
    
    Args:
        session_id (int): Database session ID
        config (dict): Test configuration
    """
    global soak_test_in_progress, current_soak_test_session
    
    # Configuration
    test_type = config.get('test_type', 'both')  # 'plc', 'robot', or 'both'
    target_cycles = config.get('cycles', 3)
    failure_threshold = config.get('failure_threshold', 10)  # Percentage
    commands_per_speed = 25
    
    # Speed intervals (seconds between commands)
    speed_intervals = [1.0, 0.8, 0.6, 0.4, 0.2, 0.1]
    
    try:
        print(f"Starting Communication Stress test - Session {session_id}")
        
        # Initialize components based on test type
        robot_ready = False
        plc_ready = False
        
        if test_type in ['robot', 'both']:
            # Test robot connectivity
            for attempt in range(3):
                if soak_test_stop_event.is_set():
                    raise Exception("Test stopped by user during robot initialization")
                    
                try:
                    success, result = c3dp_test_connectivity(complete_test=False)
                    if success:
                        robot_ready = True
                        log_soak_test_operation(
                            session_id, 'robot_init', None, True,
                            operation_data={'result': result}
                        )
                        break
                    else:
                        raise Exception(f"Robot initialization failed: {result}")
                        
                except Exception as e:
                    error_msg = f"Robot initialization attempt {attempt + 1} failed: {str(e)}"
                    print(error_msg)
                    
                    log_soak_test_error(
                        session_id, 'Robot_Communication', error_msg,
                        component='robot', recovery_action=f"Retry {attempt + 1}/3"
                    )
                    
                    if attempt >= 2:
                        raise Exception(f"Failed to initialize robot after 3 attempts")
                    
                    time.sleep(2)
        
        if test_type in ['plc', 'both']:
            # Test PLC connectivity
            for attempt in range(3):
                if soak_test_stop_event.is_set():
                    raise Exception("Test stopped by user during PLC initialization")
                    
                try:
                    result = control_panel_get_macstat()
                    if "STANDBY" in result or "SHUTDWN" in result or "MACSTAT" in result:
                        plc_ready = True
                        log_soak_test_operation(
                            session_id, 'plc_init', None, True,
                            operation_data={'result': result}
                        )
                        break
                    else:
                        raise Exception(f"PLC initialization failed: {result}")
                        
                except Exception as e:
                    error_msg = f"PLC initialization attempt {attempt + 1} failed: {str(e)}"
                    print(error_msg)
                    
                    log_soak_test_error(
                        session_id, 'PLC_Communication', error_msg,
                        component='PLC', recovery_action=f"Retry {attempt + 1}/3"
                    )
                    
                    if attempt >= 2:
                        raise Exception(f"Failed to initialize PLC after 3 attempts")
                    
                    time.sleep(2)
        
        # Main test loop
        total_operations = 0
        successful_operations = 0
        failed_operations = 0
        
        for cycle in range(1, target_cycles + 1):
            if soak_test_stop_event.is_set():
                break
                
            print(f"Starting cycle {cycle}/{target_cycles}")
            
            # Create cycle record
            cycle_id = create_soak_test_cycle(session_id, cycle)
            cycle_start_time = time.time()
            
            # Update session status
            update_soak_test_session(session_id, {
                'current_cycle': cycle,
                'current_step': f'Starting cycle {cycle}'
            })
            
            cycle_successful = 0
            cycle_failed = 0
            
            # Test each speed interval
            for speed_interval in speed_intervals:
                if soak_test_stop_event.is_set():
                    break
                    
                print(f"Testing {speed_interval}s interval (Cycle {cycle})")
                
                # Update current step
                update_soak_test_session(session_id, {
                    'current_step': f'Testing {speed_interval}s interval'
                })
                
                # Test this speed interval
                interval_success, interval_stats = test_communication_interval(
                    session_id, cycle_id, speed_interval, commands_per_speed,
                    test_type, failure_threshold
                )
                
                total_operations += interval_stats['total']
                successful_operations += interval_stats['successful']
                failed_operations += interval_stats['failed']
                
                if interval_success:
                    cycle_successful += 1
                else:
                    cycle_failed += 1
                    
                    # Check if we should stop due to high failure rate
                    failure_rate = (interval_stats['failed'] / interval_stats['total']) * 100
                    if failure_rate > failure_threshold:
                        error_msg = f"Stopping speed progression - failure rate {failure_rate:.1f}% exceeds threshold {failure_threshold}%"
                        print(error_msg)
                        
                        log_soak_test_error(
                            session_id, 'Communication_Failure', error_msg,
                            component='system', recovery_action='stop_speed_progression'
                        )
                        break
                
                # Update statistics after each interval
                current_success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0
                update_soak_test_session(session_id, {
                    'total_operations': total_operations,
                    'successful_operations': successful_operations,
                    'failed_operations': failed_operations,
                    'current_success_rate': current_success_rate
                })
            
            # Complete cycle
            cycle_duration = time.time() - cycle_start_time
            cycle_success_rate = (cycle_successful / len(speed_intervals) * 100) if len(speed_intervals) > 0 else 0
            
            # Update cycle record
            if cycle_id:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE soak_test_cycles 
                        SET end_time = ?, status = ?, total_positions = ?, 
                            successful_positions = ?, failed_positions = ?, success_rate = ?
                        WHERE id = ?
                    """, (
                        datetime.now(), 'completed', len(speed_intervals),
                        cycle_successful, cycle_failed, cycle_success_rate, cycle_id
                    ))
                    conn.commit()
            
            print(f"Cycle {cycle} completed - Success rate: {cycle_success_rate:.1f}%")
            
            # Update session
            update_soak_test_session(session_id, {
                'completed_cycles': cycle,
                'current_step': f'Completed cycle {cycle}'
            })
        
        # Test completed successfully
        final_success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0
        session_data = get_soak_test_session(session_id)
        start_time = datetime.fromisoformat(session_data['start_time'])
        total_duration = (datetime.now() - start_time).total_seconds() / 60
        
        update_soak_test_session(session_id, {
            'status': 'completed',
            'end_time': datetime.now(),
            'final_success_rate': final_success_rate,
            'duration_minutes': total_duration,
            'current_step': 'Test completed successfully'
        })
        
        print(f"Communication Stress test completed successfully - Final success rate: {final_success_rate:.1f}%")
        
    except Exception as e:
        # Test failed
        error_msg = f"Communication Stress test failed: {str(e)}"
        print(error_msg)
        
        update_soak_test_session(session_id, {
            'status': 'failed',
            'end_time': datetime.now(),
            'failure_reason': str(e),
            'current_step': 'Test failed'
        })
        
        log_soak_test_error(
            session_id, determine_soak_error_category(str(e), 'test_execution'),
            error_msg, component='system'
        )
    
    finally:
        # Cleanup
        print("Cleaning up Communication Stress test")
        
        # Reset global flags
        soak_test_in_progress = False
        current_soak_test_session = None
        soak_test_stop_event.clear()

def test_communication_interval(session_id, cycle_id, interval, commands_per_speed, test_type, failure_threshold):
    """
    Test communication at a specific interval.
    
    Args:
        session_id (int): Database session ID
        cycle_id (int): Database cycle ID
        interval (float): Time interval between commands
        commands_per_speed (int): Number of commands to send
        test_type (str): 'plc', 'robot', or 'both'
        failure_threshold (float): Failure percentage threshold
    
    Returns:
        tuple: (success, stats_dict)
    """
    stats = {'total': 0, 'successful': 0, 'failed': 0, 'timeouts': 0, 'avg_response_time': 0}
    response_times = []
    
    try:
        print(f"Testing {commands_per_speed} commands at {interval}s interval")
        
        for command_num in range(commands_per_speed):
            if soak_test_stop_event.is_set():
                break
                
            # Test PLC communication
            if test_type in ['plc', 'both']:
                plc_success, plc_time = test_single_plc_command(session_id, cycle_id, interval)
                stats['total'] += 1
                if plc_success:
                    stats['successful'] += 1
                    response_times.append(plc_time)
                else:
                    stats['failed'] += 1
                    if plc_time == -1:  # Timeout
                        stats['timeouts'] += 1
            
            # Test Robot communication
            if test_type in ['robot', 'both']:
                robot_success, robot_time = test_single_robot_command(session_id, cycle_id, interval)
                stats['total'] += 1
                if robot_success:
                    stats['successful'] += 1
                    response_times.append(robot_time)
                else:
                    stats['failed'] += 1
                    if robot_time == -1:  # Timeout
                        stats['timeouts'] += 1
            
            # Wait for the specified interval
            time.sleep(interval)
            
            # Check failure rate periodically
            if (command_num + 1) % 5 == 0:  # Check every 5 commands
                current_failure_rate = (stats['failed'] / stats['total']) * 100
                if current_failure_rate > failure_threshold:
                    print(f"Early termination: failure rate {current_failure_rate:.1f}% exceeds threshold")
                    break
        
        # Calculate statistics
        if response_times:
            stats['avg_response_time'] = sum(response_times) / len(response_times)
        
        success_rate = (stats['successful'] / stats['total'] * 100) if stats['total'] > 0 else 0
        
        # Log the interval result
        log_soak_test_operation(
            session_id, 'communication_interval', f"{interval}s", 
            success_rate >= (100 - failure_threshold),
            cycle_id=cycle_id,
            operation_data={
                'interval': interval,
                'commands_sent': stats['total'],
                'success_rate': success_rate,
                'avg_response_time': stats['avg_response_time'],
                'timeouts': stats['timeouts']
            }
        )
        
        # Return success if failure rate is below threshold
        return success_rate >= (100 - failure_threshold), stats
        
    except Exception as e:
        error_msg = f"Communication interval test failed: {str(e)}"
        print(error_msg)
        
        log_soak_test_error(
            session_id, determine_soak_error_category(str(e), 'communication_test'),
            error_msg, component='system'
        )
        
        return False, stats

def test_single_plc_command(session_id, cycle_id, interval):
    """Test a single PLC command and measure response time."""
    start_time = time.time()
    
    try:
        result = control_panel_get_macstat()
        response_time = time.time() - start_time
        
        # Check if response is valid
        if "MACSTAT" in result or "STANDBY" in result or "SHUTDWN" in result:
            return True, response_time
        else:
            # Invalid response
            log_soak_test_error(
                session_id, 'PLC_Communication', 
                f"Invalid PLC response: {result}",
                component='PLC'
            )
            return False, response_time
            
    except Exception as e:
        response_time = time.time() - start_time
        
        # Check if it's a timeout
        if "timeout" in str(e).lower():
            return False, -1  # Special value for timeout
        else:
            log_soak_test_error(
                session_id, 'PLC_Communication',
                f"PLC command failed: {str(e)}",
                component='PLC'
            )
            return False, response_time

def test_single_robot_command(session_id, cycle_id, interval):
    """Test a single robot command and measure response time."""
    global global_robot
    
    start_time = time.time()
    
    try:
        if not global_robot:
            raise Exception("Robot not initialized")
        
        # Send M114 command to get position
        if global_robot.test_connection():
            global_robot.get_current_position()
            response_time = time.time() - start_time
            
            # Check if we got a valid position
            if global_robot.has_been_homed:
                return True, response_time
            else:
                log_soak_test_error(
                    session_id, 'Robot_Communication',
                    "Robot position not available",
                    component='robot'
                )
                return False, response_time
        else:
            response_time = time.time() - start_time
            log_soak_test_error(
                session_id, 'Robot_Communication',
                "Robot connection test failed",
                component='robot'
            )
            return False, response_time
            
    except Exception as e:
        response_time = time.time() - start_time
        
        # Check if it's a timeout
        if "timeout" in str(e).lower():
            return False, -1  # Special value for timeout
        else:
            log_soak_test_error(
                session_id, 'Robot_Communication',
                f"Robot command failed: {str(e)}",
                component='robot'
            )
            return False, response_time

#endregion

#region - TEM Disk cycle soak test functions

def soak_test_tem_cycling(session_id, config):
    """
    Main TEM Disk Cycling soak test function.
    Tests bidirectional TEM disk operations with laser verification.
    
    Args:
        session_id (int): Database session ID
        config (dict): Test configuration
    """
    global soak_test_in_progress, current_soak_test_session
    
    # Define test mapping (TC -> TE bidirectional)
    disk_pairs = [
        ('TC1', 'TE1'), ('TC2', 'TE2'), ('TC3', 'TE3'), ('TC4', 'TE4'), ('TC5', 'TE5'),
        ('TC6', 'TE6'), ('TC7', 'TE7'), ('TC8', 'TE8'), ('TC9', 'TE9'), ('TC10', 'TE10')
    ]
    
    target_cycles = config.get('cycles', 2)
    max_retries = config.get('max_retries', 3)
    failure_handling = config.get('failure_handling', 'skip')
    skip_laser = config.get('skip_laser', False)
    
    try:
        print(f"Starting TEM Disk Cycling test - Session {session_id}")
        
        # Initialize robot
        robot_ready = False
        retry_count = 0
        max_robot_retries = 3
        
        while not robot_ready and retry_count < max_robot_retries:
            if soak_test_stop_event.is_set():
                raise Exception("Test stopped by user during initialization")
                
            try:
                success, result = c3dp_test_connectivity(complete_test=False)
                if success:
                    robot_ready = True
                    log_soak_test_operation(
                        session_id, 'robot_init', None, True, 
                        operation_data={'result': result}
                    )
                else:
                    raise Exception(f"Robot initialization failed: {result}")
                    
            except Exception as e:
                retry_count += 1
                error_msg = f"Robot initialization attempt {retry_count} failed: {str(e)}"
                print(error_msg)
                
                log_soak_test_error(
                    session_id, 'Robot_Communication', error_msg,
                    component='robot', recovery_action=f"Retry {retry_count}/{max_robot_retries}"
                )
                
                if retry_count >= max_robot_retries:
                    raise Exception(f"Failed to initialize robot after {max_robot_retries} attempts")
                
                time.sleep(5)
        
        # Home the robot
        if not soak_test_stop_event.is_set():
            try:
                device_step_zero()
                log_soak_test_operation(session_id, 'robot_home', None, True)
            except Exception as e:
                error_msg = f"Robot homing failed: {str(e)}"
                log_soak_test_error(session_id, 'Robot_Movement', error_msg, component='robot')
                raise Exception(error_msg)
        
        # Main test loop
        total_operations = 0
        successful_operations = 0
        failed_operations = 0
        
        for cycle in range(1, target_cycles + 1):
            if soak_test_stop_event.is_set():
                break
                
            print(f"Starting cycle {cycle}/{target_cycles}")
            
            # Create cycle record
            cycle_id = create_soak_test_cycle(session_id, cycle)
            cycle_start_time = time.time()
            
            # Update session status
            update_soak_test_session(session_id, {
                'current_cycle': cycle,
                'current_step': f'Starting cycle {cycle}'
            })
            
            cycle_successful = 0
            cycle_failed = 0
            
            # Phase 1: Move all disks from TC to TE (clean to used)
            print(f"Phase 1: Moving disks TC→TE (Cycle {cycle})")
            update_soak_test_session(session_id, {
                'current_step': f'Phase 1: TC→TE transfers'
            })
            
            for origin, destination in disk_pairs:
                if soak_test_stop_event.is_set():
                    break
                    
                success = perform_tem_disk_transfer(
                    session_id, cycle_id, origin, destination, 
                    max_retries, failure_handling, skip_laser
                )
                
                total_operations += 1
                if success:
                    successful_operations += 1
                    cycle_successful += 1
                else:
                    failed_operations += 1
                    cycle_failed += 1
                    if failure_handling == 'stop':
                        raise Exception(f"Test stopped due to failure at {origin}→{destination}")
                
                # Update statistics
                current_success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0
                update_soak_test_session(session_id, {
                    'total_operations': total_operations,
                    'successful_operations': successful_operations,
                    'failed_operations': failed_operations,
                    'current_success_rate': current_success_rate,
                    'current_position': f"{origin}→{destination}"
                })
            
            # Short pause between phases
            if not soak_test_stop_event.is_set():
                time.sleep(2)
            
            # Phase 2: Move all disks from TE back to TC (used to clean)
            print(f"Phase 2: Moving disks TE→TC (Cycle {cycle})")
            update_soak_test_session(session_id, {
                'current_step': f'Phase 2: TE→TC returns'
            })
            
            for origin, destination in disk_pairs:
                if soak_test_stop_event.is_set():
                    break
                    
                # Reverse the direction for return trip
                success = perform_tem_disk_transfer(
                    session_id, cycle_id, destination, origin,  # TE→TC
                    max_retries, failure_handling, skip_laser
                )
                
                total_operations += 1
                if success:
                    successful_operations += 1
                    cycle_successful += 1
                else:
                    failed_operations += 1
                    cycle_failed += 1
                    if failure_handling == 'stop':
                        raise Exception(f"Test stopped due to failure at {destination}→{origin}")
                
                # Update statistics
                current_success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0
                update_soak_test_session(session_id, {
                    'total_operations': total_operations,
                    'successful_operations': successful_operations,
                    'failed_operations': failed_operations,
                    'current_success_rate': current_success_rate,
                    'current_position': f"{destination}→{origin}"
                })
            
            # Complete cycle
            cycle_duration = time.time() - cycle_start_time
            total_positions_in_cycle = len(disk_pairs) * 2  # Both directions
            cycle_success_rate = (cycle_successful / total_positions_in_cycle * 100) if total_positions_in_cycle > 0 else 0
            
            # Update cycle record
            if cycle_id:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE soak_test_cycles 
                        SET end_time = ?, status = ?, total_positions = ?, 
                            successful_positions = ?, failed_positions = ?, success_rate = ?
                        WHERE id = ?
                    """, (
                        datetime.now(), 'completed', total_positions_in_cycle,
                        cycle_successful, cycle_failed, cycle_success_rate, cycle_id
                    ))
                    conn.commit()
            
            print(f"Cycle {cycle} completed - Success rate: {cycle_success_rate:.1f}%")
            
            # Update session
            update_soak_test_session(session_id, {
                'completed_cycles': cycle,
                'current_step': f'Completed cycle {cycle}'
            })
        
        # Test completed successfully
        final_success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0
        session_data = get_soak_test_session(session_id)
        start_time = datetime.fromisoformat(session_data['start_time'])
        total_duration = (datetime.now() - start_time).total_seconds() / 60
        
        update_soak_test_session(session_id, {
            'status': 'completed',
            'end_time': datetime.now(),
            'final_success_rate': final_success_rate,
            'duration_minutes': total_duration,
            'current_step': 'Test completed successfully'
        })
        
        print(f"TEM Disk Cycling test completed successfully - Final success rate: {final_success_rate:.1f}%")
        
    except Exception as e:
        # Test failed
        error_msg = f"TEM Disk Cycling test failed: {str(e)}"
        print(error_msg)
        
        update_soak_test_session(session_id, {
            'status': 'failed',
            'end_time': datetime.now(),
            'failure_reason': str(e),
            'current_step': 'Test failed'
        })
        
        log_soak_test_error(
            session_id, determine_soak_error_category(str(e), 'test_execution'),
            error_msg, component='system'
        )
    
    finally:
        # Cleanup
        print("Cleaning up TEM Disk Cycling test")
        
        try:
            # Return robot to home position
            device_step_final()
        except Exception as e:
            print(f"Error during cleanup: {e}")
        
        # Reset global flags
        soak_test_in_progress = False
        current_soak_test_session = None
        soak_test_stop_event.clear()

def perform_tem_disk_transfer(session_id, cycle_id, origin, destination, max_retries, failure_handling, skip_laser):
    """
    Perform a single TEM disk transfer operation.
    
    Args:
        session_id (int): Database session ID
        cycle_id (int): Database cycle ID
        origin (str): Origin position (TC# or TE#)
        destination (str): Destination position
        max_retries (int): Maximum retry attempts
        failure_handling (str): Failure handling strategy
        skip_laser (bool): Whether to skip laser verification
    
    Returns:
        bool: True if successful, False if failed
    """
    global global_robot
    
    retry_count = 0
    
    while retry_count <= max_retries:
        try:
            if not global_robot:
                raise Exception("Robot not initialized")
            
            print(f"Transferring disk {origin}→{destination} (attempt {retry_count + 1})")
            
            operation_start_time = time.time()
            
            # Step 1: Move to standby position
            global_robot.speed = SPEED_NORMAL
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Step 2: Determine if this is clean or used disk area
            if origin.startswith('TC'):
                # Moving from clean area (TC)
                disk_positions = global_robot.clean_disk_pos
                disk_z_positions = ['TCTRAY_Z1', 'TCTRAY_Z2', 'TCTRAY_Z3']
            else:
                # Moving from used area (TE)
                disk_positions = global_robot.used_disk_pos
                disk_z_positions = ['TETRAY_Z1', 'TETRAY_Z2', 'TETRAY_Z3']
            
            # Step 3: Open TEM grid holder and turn on vacuum
            global_robot.moveto(x=disk_positions[origin][0])
            control_panel_tem_grid_holder_open()
            time.sleep(1.5)
            control_panel_vacuum("TEM", True)
            
            # Step 4: Pick up disk
            global_robot.moveto(*disk_positions[origin])
            global_robot.moveto(*disk_positions[disk_z_positions[0]])
            global_robot.speed = SPEED_LOW
            global_robot.moveto(*disk_positions[disk_z_positions[1]])
            global_robot.speed = SPEED_VLOW
            global_robot.moveto(*disk_positions[disk_z_positions[2]])
            global_robot.moveto(*disk_positions[disk_z_positions[1]])
            global_robot.speed = SPEED_NORMAL
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            # Step 5: Laser verification (if enabled)
            disk_picked = True
            if not skip_laser:
                print(f"Checking laser detection for {origin}")
                global_robot.moveto(*global_robot.equipment_pos["LASER_TEM"])
                global_robot.moveto(*global_robot.equipment_pos["LASER_TEM_Z1"])
                
                laser_result = control_panel_laser_status()
                if laser_result != "LASER1":
                    disk_picked = False
                    print(f"Laser detection failed for {origin}")
                    log_soak_test_error(
                        session_id, 'Laser_Detection',
                        f"Disk not detected at {origin} - Expected LASER1, got {laser_result}",
                        position=origin, component='laser'
                    )
                
                global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            
            if not disk_picked:
                # Turn off vacuum and retry or fail
                control_panel_vacuum("TEM", False)
                control_panel_tem_grid_holder_close()
                
                if failure_handling == 'retry' and retry_count < max_retries:
                    retry_count += 1
                    print(f"Retrying {origin}→{destination} (attempt {retry_count + 1})")
                    continue
                else:
                    operation_duration = time.time() - operation_start_time
                    log_soak_test_operation(
                        session_id, 'disk_transfer', f"{origin}→{destination}", False,
                        cycle_id=cycle_id, retry_count=retry_count,
                        error_message=f"Disk not picked from {origin}",
                        duration_seconds=operation_duration
                    )
                    return False
            
            # Step 6: Close grid holder
            time.sleep(1)
            control_panel_tem_grid_holder_close()
            time.sleep(1)
            
            # Step 7: Move to destination area
            if destination.startswith('TC'):
                # Moving to clean area
                dest_positions = global_robot.clean_disk_pos
                dest_z_positions = ['TCTRAY_Z1', 'TCTRAY_Z2', 'TCTRAY_Z3']
            else:
                # Moving to used area
                dest_positions = global_robot.used_disk_pos
                dest_z_positions = ['TETRAY_Z1', 'TETRAY_Z2', 'TETRAY_Z3']
            
            # Step 8: Place disk at destination (separate X/Y movement to avoid contamination)
            global_robot.moveto(x=dest_positions[destination][0])
            control_panel_tem_grid_holder_open()
            time.sleep(1)
            global_robot.moveto(y=dest_positions[destination][1])
            global_robot.moveto(*dest_positions[dest_z_positions[0]])
            global_robot.speed = SPEED_LOW
            global_robot.moveto(*dest_positions[dest_z_positions[1]])
            global_robot.speed = SPEED_VLOW
            global_robot.moveto(*dest_positions[dest_z_positions[2]])
            
            # Turn off vacuum to release disk
            control_panel_vacuum("TEM", False)
            time.sleep(PAUSE_VAC)
            
            global_robot.moveto(*dest_positions[dest_z_positions[1]])
            global_robot.speed = SPEED_NORMAL
            global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            time.sleep(1)
            control_panel_tem_grid_holder_close()
            time.sleep(1)
            
            # Step 9: Return to home position
            global_robot.moveto(x=global_robot.intermediate_pos["HOME"][0])
            global_robot.moveto(y=global_robot.intermediate_pos["HOME"][1])
            
            # Log successful operation
            operation_duration = time.time() - operation_start_time
            log_soak_test_operation(
                session_id, 'disk_transfer', f"{origin}→{destination}", True,
                cycle_id=cycle_id, retry_count=retry_count,
                duration_seconds=operation_duration,
                operation_data={'laser_verification': not skip_laser}
            )
            
            print(f"Successfully transferred disk {origin}→{destination}")
            return True
            
        except Exception as e:
            # Handle any errors during the transfer
            error_msg = f"Disk transfer {origin}→{destination} failed: {str(e)}"
            print(error_msg)
            
            # Ensure cleanup in case of error
            try:
                control_panel_vacuum("TEM", False)
                control_panel_tem_grid_holder_close()
                if global_robot:
                    global_robot.speed = SPEED_NORMAL
                    global_robot.moveto(*global_robot.intermediate_pos["ZHOME"])
            except Exception as cleanup_error:
                print(f"Error during cleanup: {cleanup_error}")
            
            # Log the error
            log_soak_test_error(
                session_id, determine_soak_error_category(str(e), 'disk_transfer'),
                error_msg, position=f"{origin}→{destination}", component='robot'
            )
            
            # Decide whether to retry
            if failure_handling == 'retry' and retry_count < max_retries:
                retry_count += 1
                print(f"Retrying {origin}→{destination} (attempt {retry_count + 1})")
                time.sleep(1)  # Brief pause before retry
                continue
            else:
                # Log failed operation
                operation_duration = time.time() - operation_start_time
                log_soak_test_operation(
                    session_id, 'disk_transfer', f"{origin}→{destination}", False,
                    cycle_id=cycle_id, retry_count=retry_count,
                    error_message=error_msg, duration_seconds=operation_duration
                )
                return False
    
    # If we exit the while loop, all retries failed
    return False

#endregion


# Map function names to handlers
function_map = {
    'button': button_action,
    'sem_process': sem_process_action,
    'tem_process': tem_process_action,
    'tem_manual_prepare': tem_manual_prepare,
    'tem_manual_expose': tem_manual_expose,
    'tem_manual_complete': tem_manual_complete,
    'c3dp_test_connectivity': c3dp_test_connectivity,
    'c3dp_test_connectivity_machine_test_page': c3dp_test_connectivity_machine_test_page,
    'server_test_connectivity': server_test_connectivity,
    'control_panel_get_macstat': control_panel_get_macstat,
    'control_panel_standby': control_panel_standby,
    'control_panel_shutdown': control_panel_shutdown,
    'control_panel_sem_stage_open': control_panel_sem_stage_open,
    'control_panel_sem_stage_close': control_panel_sem_stage_close,
    'control_panel_tem_grid_holder_open': control_panel_tem_grid_holder_open,
    'control_panel_tem_grid_holder_close': control_panel_tem_grid_holder_close,
    'control_panel_gripper_home': control_panel_gripper_home,
    'control_panel_gripper_close': control_panel_gripper_close,
    'control_panel_rotator': control_panel_rotator,
    'control_panel_vibration_motor_1_on': control_panel_vibration_motor_1_on,
    'control_panel_vibration_motor_1_off': control_panel_vibration_motor_1_off,
    'control_panel_vibration_motor_2_on': control_panel_vibration_motor_2_on,
    'control_panel_vibration_motor_2_off': control_panel_vibration_motor_2_off,
    'control_panel_vibration_motor_both_on': control_panel_vibration_motor_both_on,
    'control_panel_vibration_motor_both_off': control_panel_vibration_motor_both_off,
    'control_panel_vibration_motor_all_off': control_panel_vibration_motor_all_off,
    'device_extend_bed': device_extend_bed,
    'device_retract_bed': device_retract_bed,
    'robot_manual_move': move_robot_manual,
    'robot_manual_home': home_robot_manual,
    'send_manual_plc_command': send_manual_plc_command,
    'state_check': state_check,
    'get_sem_positions': get_sem_position_status,
    'get_tem_positions': get_tem_position_status,
    'clear_sem_memory': clear_sem_memory,
    'clear_tem_memory': clear_tem_memory
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
    elif page == 'tem-manual':
        return render_template('pages/tem_manual.html')
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
def tem_tray_page():
    return render_template('tem_tray.html')

@app.route('/tem_manual')
def tem_manual_page():
    global tem_manual_state
    tem_manual_state = "idle"
    return render_template('tem_manual.html')

@socketio.on('call_function')
def handle_socket_function(data):
    function_name = data.get('function')
    result = dispatch_action({'function': function_name, 'id': function_name})
    socketio.emit('function_response', {'result': result})

@app.route('/handle_function', methods=['POST'])
def handle_function():
    data = request.json
    print("Received HTTP request:", data)  # Debug log
    
    if data.get('function') not in function_map:
        print(f"WARNING: Unknown function '{data.get('function')}' not found in function_map!")
    
    result = dispatch_action(data)
    return jsonify({"status": "success", "message": result})

@app.route('/advanced_control')
def advanced_manual_control():
    return render_template('advanced_control.html')

#region - routes related to statistics

@app.route('/stats')
def stats_dashboard():
    """Enhanced statistics dashboard using template with recent operations."""
    try:
        # Gather all existing statistics data
        success_rates = get_success_rate_by_process_type(30)  # Last 30 days
        common_errors = get_most_common_errors(10)
        process_counts = get_process_counts_by_time_period('day', 7)  # Last 7 days
        error_categories = get_error_categories_summary()
        
        # Calculate summary statistics (your existing function)
        summary = calculate_summary_stats(success_rates, error_categories)
        
        # Add performance metrics and component reliability
        performance_metrics = get_performance_metrics()
        component_reliability = get_component_reliability()
        
        # Get new recent operations data
        recent_operations = get_recent_operations_with_details(10)
        
        # Add percentage calculation for error categories (your existing logic)
        total_errors = sum(cat['count'] for cat in error_categories)
        for category in error_categories:
            category['percentage'] = (category['count'] / total_errors * 100) if total_errors > 0 else 0
        
        # Render the template with all data (existing + new)
        return render_template('stats.html',
                             success_rates=success_rates,
                             common_errors=common_errors,
                             process_counts=process_counts,
                             error_categories=error_categories,
                             performance_metrics=performance_metrics,
                             component_reliability=component_reliability,
                             recent_operations=recent_operations,  # NEW: Recent operations data
                             summary=summary,
                             last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
    except Exception as e:
        print(f"Error loading statistics: {e}")
        return render_template('stats.html',
                             success_rates=[],
                             common_errors=[],
                             process_counts=[],
                             error_categories=[],
                             performance_metrics=[],
                             component_reliability=[],
                             recent_operations=[],  # NEW: Empty recent operations
                             summary={},
                             last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                             error_message=str(e))

def calculate_summary_stats(success_rates, error_categories):
    """Calculate summary statistics for the dashboard."""
    total_processes = sum(rate['total_runs'] for rate in success_rates)
    total_successful = sum(rate['successful_runs'] for rate in success_rates)
    total_errors = sum(cat['count'] for cat in error_categories)
    
    # Calculate overall success rate
    overall_success_rate = (total_successful / total_processes * 100) if total_processes > 0 else 0
    
    # Get average duration from performance metrics
    try:
        performance_metrics = get_performance_metrics()
        if performance_metrics:
            avg_duration = sum(metric['avg_duration'] for metric in performance_metrics) / len(performance_metrics)
        else:
            avg_duration = 0
    except:
        avg_duration = 0
    
    return {
        'total_processes': total_processes,
        'overall_success_rate': overall_success_rate,
        'total_errors': total_errors,
        'avg_duration': avg_duration
    }

@app.route('/api/stats/success-rates')
def api_success_rates():
    """API endpoint for success rates."""
    try:
        days = request.args.get('days', 30, type=int)
        data = get_success_rate_by_process_type(days)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats/errors')
def api_common_errors():
    """API endpoint for common errors."""
    try:
        limit = request.args.get('limit', 10, type=int)
        data = get_most_common_errors(limit)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats/process-counts')
def api_process_counts():
    """API endpoint for process counts by time period."""
    try:
        period = request.args.get('period', 'day')
        days = request.args.get('days', 7, type=int)
        data = get_process_counts_by_time_period(period, days)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats/error-categories')
def api_error_categories():
    """API endpoint for error categories summary."""
    try:
        data = get_error_categories_summary()
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats/export')
def api_export_data():
    """Export statistics data as JSON."""
    try:
        export_data = {
            'success_rates': get_success_rate_by_process_type(30),
            'common_errors': get_most_common_errors(20),
            'process_counts': get_process_counts_by_time_period('day', 30),
            'error_categories': get_error_categories_summary(),
            'performance_metrics': get_performance_metrics(),
            'component_reliability': get_component_reliability(),
            'export_timestamp': datetime.now().isoformat()
        }
        
        response = jsonify(export_data)
        response.headers['Content-Disposition'] = f'attachment; filename=em_autoprep_stats_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        return response
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
#endregion

#region - routes related to soak tests

@app.route('/soak_tests')
def soak_test_dashboard():
    """Main soak test dashboard page with database integration."""
    try:
        # Get current running test from database
        current_test = get_current_soak_test_session()
        
        # Get recent test history
        recent_tests = get_recent_soak_tests(limit=10)
        
        # Get success rate statistics
        stats = get_soak_test_statistics()
        
        return render_template('soak_tests/dashboard.html',
                             current_test_running=current_test,
                             recent_tests=recent_tests,
                             stats=stats)
                             
    except Exception as e:
        print(f"Error loading soak test dashboard: {e}")
        return render_template('soak_tests/dashboard.html',
                             current_test_running=None,
                             recent_tests=[],
                             stats={},
                             error_message=str(e))

@app.route('/soak_tests/sem_pick_place')
def sem_pick_place_test_page():
    """SEM Pick & Place soak test page with database integration."""
    try:
        # Get current test session from database
        current_session = get_current_soak_test_session()
        test_session = None
        
        if (current_session and 
            current_session.get('test_type') == 'sem_pick_place'):
            
            # Enhance session data with computed fields
            test_session = current_session.copy()
            
            # Calculate elapsed time
            if test_session.get('start_time'):
                start_time = datetime.fromisoformat(test_session['start_time'])
                elapsed = datetime.now() - start_time
                test_session['elapsed_time_minutes'] = elapsed.total_seconds() / 60
            
            # Calculate estimated completion
            if (test_session.get('current_cycle') and 
                test_session.get('target_cycles') and
                test_session.get('elapsed_time_minutes')):
                
                progress_ratio = test_session['current_cycle'] / test_session['target_cycles']
                if progress_ratio > 0:
                    total_estimated_minutes = test_session['elapsed_time_minutes'] / progress_ratio
                    remaining_minutes = total_estimated_minutes - test_session['elapsed_time_minutes']
                    completion_time = datetime.now() + timedelta(minutes=remaining_minutes)
                    test_session['estimated_completion'] = completion_time.strftime('%H:%M')
        
        # Get recent errors for this test session
        recent_errors = []
        if test_session:
            recent_errors = get_soak_test_errors_for_session(test_session['id'], limit=10)
        
        return render_template('soak_tests/sem_pick_place.html',
                             test_session=test_session,
                             recent_errors=recent_errors)
                             
    except Exception as e:
        print(f"Error loading SEM pick & place test page: {e}")
        return render_template('soak_tests/sem_pick_place.html',
                             test_session=None,
                             recent_errors=[],
                             error_message=str(e))

@app.route('/soak_tests/sem_pick_place/start', methods=['POST'])
def start_sem_pick_place_test():
    """Start a new SEM Pick & Place soak test with database integration."""
    global soak_test_in_progress, current_soak_test_session, current_soak_test_thread
    
    try:
        # Check if another test is running
        if soak_test_in_progress or get_current_soak_test_session():
            return jsonify({
                'success': False,
                'message': 'Another soak test is currently running. Please wait for it to complete.'
            })
        
        # Get and validate configuration
        config = request.json
        cycles = int(config.get('cycles', 5))
        max_retries = int(config.get('max_retries', 3))
        failure_handling = config.get('failure_handling', 'skip')
        recovery_mode = config.get('recovery_mode', 'continue')
        
        # Validation
        if cycles < 1 or cycles > 1000:
            return jsonify({
                'success': False,
                'message': 'Number of cycles must be between 1 and 1000.'
            })
        
        if max_retries < 1 or max_retries > 10:
            return jsonify({
                'success': False,
                'message': 'Max retries must be between 1 and 10.'
            })
        
        if failure_handling not in ['skip', 'retry', 'stop']:
            return jsonify({
                'success': False,
                'message': 'Invalid failure handling option.'
            })
        
        if recovery_mode not in ['continue', 'restart']:
            return jsonify({
                'success': False,
                'message': 'Invalid recovery mode option.'
            })
        
        # Create database session
        session_id = create_soak_test_session('sem_pick_place', config)
        if not session_id:
            return jsonify({
                'success': False,
                'message': 'Failed to create test session in database.'
            })
        
        # Set global flags
        soak_test_in_progress = True
        soak_test_stop_event.clear()
        
        # Start test in background thread
        current_soak_test_thread = threading.Thread(
            target=soak_test_sem_pick_place,
            args=(session_id, config),
            daemon=True
        )
        current_soak_test_thread.start()
        
        print(f"Started SEM Pick & Place test - Session ID: {session_id}")
        
        return jsonify({
            'success': True,
            'message': 'Test started successfully',
            'session_id': session_id
        })
        
    except Exception as e:
        # Reset flags on error
        soak_test_in_progress = False
        print(f"Error starting SEM pick & place test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error starting test: {str(e)}'
        })

@app.route('/soak_tests/sem_pick_place/pause', methods=['POST'])
def pause_sem_pick_place_test():
    """Pause the current SEM Pick & Place test."""
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'sem_pick_place' or
            current_session.get('status') != 'running'):
            return jsonify({
                'success': False,
                'message': 'No running SEM Pick & Place test found.'
            })
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'paused',
            'current_step': 'Test paused by user'
        })
        
        # Note: The actual pausing logic would need to be implemented in the test thread
        # For now, we just update the database status
        
        print("SEM Pick & Place test paused")
        return jsonify({
            'success': True,
            'message': 'Test paused successfully'
        })
        
    except Exception as e:
        print(f"Error pausing SEM pick & place test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error pausing test: {str(e)}'
        })

@app.route('/soak_tests/sem_pick_place/resume', methods=['POST'])
def resume_sem_pick_place_test():
    """Resume the paused SEM Pick & Place test."""
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'sem_pick_place' or
            current_session.get('status') != 'paused'):
            return jsonify({
                'success': False,
                'message': 'No paused SEM Pick & Place test found.'
            })
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'running',
            'current_step': 'Test resumed by user'
        })
        
        # Note: The actual resuming logic would need to be implemented in the test thread
        # For now, we just update the database status
        
        print("SEM Pick & Place test resumed")
        return jsonify({
            'success': True,
            'message': 'Test resumed successfully'
        })
        
    except Exception as e:
        print(f"Error resuming SEM pick & place test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error resuming test: {str(e)}'
        })

@app.route('/soak_tests/sem_pick_place/stop', methods=['POST'])
def stop_sem_pick_place_test():
    """Stop the current SEM Pick & Place test."""
    global soak_test_in_progress, current_soak_test_session
    
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'sem_pick_place'):
            return jsonify({
                'success': False,
                'message': 'No SEM Pick & Place test found.'
            })
        
        # Signal the test thread to stop
        soak_test_stop_event.set()
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'stopped',
            'end_time': datetime.now(),
            'current_step': 'Test stopped by user',
            'failure_reason': 'User requested stop'
        })
        
        # Reset global flags
        soak_test_in_progress = False
        current_soak_test_session = None
        
        print("SEM Pick & Place test stopped")
        return jsonify({
            'success': True,
            'message': 'Test stopped successfully'
        })
        
    except Exception as e:
        print(f"Error stopping SEM pick & place test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error stopping test: {str(e)}'
        })

@app.route('/soak_tests/communication')
def communication_test_page():
    """Communication Stress test page with database integration."""
    try:
        # Get current test session from database
        current_session = get_current_soak_test_session()
        test_session = None
        
        if (current_session and 
            current_session.get('test_type') == 'communication'):
            
            # Enhance session data with computed fields
            test_session = current_session.copy()
            
            # Calculate elapsed time
            if test_session.get('start_time'):
                start_time = datetime.fromisoformat(test_session['start_time'])
                elapsed = datetime.now() - start_time
                test_session['elapsed_time_minutes'] = elapsed.total_seconds() / 60
            
            # Calculate estimated completion
            if (test_session.get('current_cycle') and 
                test_session.get('target_cycles') and
                test_session.get('elapsed_time_minutes')):
                
                progress_ratio = test_session['current_cycle'] / test_session['target_cycles']
                if progress_ratio > 0:
                    total_estimated_minutes = test_session['elapsed_time_minutes'] / progress_ratio
                    remaining_minutes = total_estimated_minutes - test_session['elapsed_time_minutes']
                    completion_time = datetime.now() + timedelta(minutes=remaining_minutes)
                    test_session['estimated_completion'] = completion_time.strftime('%H:%M')
            
            # Parse test parameters for display
            if test_session.get('test_parameters'):
                try:
                    params = json.loads(test_session['test_parameters'])
                    test_session['test_type'] = params.get('test_type', 'both')
                    test_session['failure_threshold'] = params.get('failure_threshold', 10)
                except:
                    pass
        
        # Get recent errors for this test session
        recent_errors = []
        if test_session:
            recent_errors = get_soak_test_errors_for_session(test_session['id'], limit=10)
        
        return render_template('soak_tests/communication.html',
                             test_session=test_session,
                             recent_errors=recent_errors)
                             
    except Exception as e:
        print(f"Error loading Communication test page: {e}")
        return render_template('soak_tests/communication.html',
                             test_session=None,
                             recent_errors=[],
                             error_message=str(e))

@app.route('/soak_tests/communication/start', methods=['POST'])
def start_communication_test():
    """Start a new Communication Stress test with database integration."""
    global soak_test_in_progress, current_soak_test_session, current_soak_test_thread
    
    try:
        # Check if another test is running
        if soak_test_in_progress or get_current_soak_test_session():
            return jsonify({
                'success': False,
                'message': 'Another soak test is currently running. Please wait for it to complete.'
            })
        
        # Get and validate configuration
        config = request.json
        cycles = int(config.get('cycles', 3))
        failure_threshold = int(config.get('failure_threshold', 10))
        test_type = config.get('test_type', 'both')
        
        # Validation
        if cycles < 1 or cycles > 10:
            return jsonify({
                'success': False,
                'message': 'Number of cycles must be between 1 and 10.'
            })
        
        if failure_threshold < 5 or failure_threshold > 50:
            return jsonify({
                'success': False,
                'message': 'Failure threshold must be between 5% and 50%.'
            })
        
        if test_type not in ['plc', 'robot', 'both']:
            return jsonify({
                'success': False,
                'message': 'Invalid test type.'
            })
        
        # Create database session
        session_id = create_soak_test_session('communication', config)
        if not session_id:
            return jsonify({
                'success': False,
                'message': 'Failed to create test session in database.'
            })
        
        # Set global flags
        soak_test_in_progress = True
        soak_test_stop_event.clear()
        
        # Start test in background thread
        current_soak_test_thread = threading.Thread(
            target=soak_test_communication_stress,
            args=(session_id, config),
            daemon=True
        )
        current_soak_test_thread.start()
        
        print(f"Started Communication Stress test - Session ID: {session_id}")
        
        return jsonify({
            'success': True,
            'message': 'Test started successfully',
            'session_id': session_id
        })
        
    except Exception as e:
        # Reset flags on error
        soak_test_in_progress = False
        print(f"Error starting Communication test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error starting test: {str(e)}'
        })

@app.route('/soak_tests/communication/pause', methods=['POST'])
def pause_communication_test():
    """Pause the current Communication Stress test."""
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'communication' or
            current_session.get('status') != 'running'):
            return jsonify({
                'success': False,
                'message': 'No running Communication test found.'
            })
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'paused',
            'current_step': 'Test paused by user'
        })
        
        print("Communication test paused")
        return jsonify({
            'success': True,
            'message': 'Test paused successfully'
        })
        
    except Exception as e:
        print(f"Error pausing Communication test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error pausing test: {str(e)}'
        })

@app.route('/soak_tests/communication/resume', methods=['POST'])
def resume_communication_test():
    """Resume the paused Communication Stress test."""
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'communication' or
            current_session.get('status') != 'paused'):
            return jsonify({
                'success': False,
                'message': 'No paused Communication test found.'
            })
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'running',
            'current_step': 'Test resumed by user'
        })
        
        print("Communication test resumed")
        return jsonify({
            'success': True,
            'message': 'Test resumed successfully'
        })
        
    except Exception as e:
        print(f"Error resuming Communication test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error resuming test: {str(e)}'
        })

@app.route('/soak_tests/communication/stop', methods=['POST'])
def stop_communication_test():
    """Stop the current Communication Stress test."""
    global soak_test_in_progress, current_soak_test_session
    
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'communication'):
            return jsonify({
                'success': False,
                'message': 'No Communication test found.'
            })
        
        # Signal the test thread to stop
        soak_test_stop_event.set()
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'stopped',
            'end_time': datetime.now(),
            'current_step': 'Test stopped by user',
            'failure_reason': 'User requested stop'
        })
        
        # Reset global flags
        soak_test_in_progress = False
        current_soak_test_session = None
        
        print("Communication test stopped")
        return jsonify({
            'success': True,
            'message': 'Test stopped successfully'
        })
        
    except Exception as e:
        print(f"Error stopping Communication test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error stopping test: {str(e)}'
        })

@app.route('/soak_tests/tem_cycling')
def tem_cycling_test_page():
    """TEM Disk Cycling soak test page with database integration."""
    try:
        # Get current test session from database
        current_session = get_current_soak_test_session()
        test_session = None
        
        if (current_session and 
            current_session.get('test_type') == 'tem_cycling'):
            
            # Enhance session data with computed fields
            test_session = current_session.copy()
            
            # Calculate elapsed time
            if test_session.get('start_time'):
                start_time = datetime.fromisoformat(test_session['start_time'])
                elapsed = datetime.now() - start_time
                test_session['elapsed_time_minutes'] = elapsed.total_seconds() / 60
            
            # Calculate estimated completion
            if (test_session.get('current_cycle') and 
                test_session.get('target_cycles') and
                test_session.get('elapsed_time_minutes')):
                
                progress_ratio = test_session['current_cycle'] / test_session['target_cycles']
                if progress_ratio > 0:
                    total_estimated_minutes = test_session['elapsed_time_minutes'] / progress_ratio
                    remaining_minutes = total_estimated_minutes - test_session['elapsed_time_minutes']
                    completion_time = datetime.now() + timedelta(minutes=remaining_minutes)
                    test_session['estimated_completion'] = completion_time.strftime('%H:%M')
            
            # Parse test parameters for display
            if test_session.get('test_parameters'):
                try:
                    params = json.loads(test_session['test_parameters'])
                    test_session['skip_laser'] = params.get('skip_laser', False)
                    test_session['failure_handling'] = params.get('failure_handling', 'skip')
                    test_session['max_retries'] = params.get('max_retries', 3)
                except:
                    pass
        
        # Get recent errors for this test session
        recent_errors = []
        if test_session:
            recent_errors = get_soak_test_errors_for_session(test_session['id'], limit=10)
        
        return render_template('soak_tests/tem_cycling.html',
                             test_session=test_session,
                             recent_errors=recent_errors)
                             
    except Exception as e:
        print(f"Error loading TEM cycling test page: {e}")
        return render_template('soak_tests/tem_cycling.html',
                             test_session=None,
                             recent_errors=[],
                             error_message=str(e))

@app.route('/soak_tests/tem_cycling/start', methods=['POST'])
def start_tem_cycling_test():
    """Start a new TEM Disk Cycling soak test with database integration."""
    global soak_test_in_progress, current_soak_test_session, current_soak_test_thread
    
    try:
        # Check if another test is running
        if soak_test_in_progress or get_current_soak_test_session():
            return jsonify({
                'success': False,
                'message': 'Another soak test is currently running. Please wait for it to complete.'
            })
        
        # Get and validate configuration
        config = request.json
        cycles = int(config.get('cycles', 2))
        max_retries = int(config.get('max_retries', 3))
        failure_handling = config.get('failure_handling', 'skip')
        skip_laser = config.get('skip_laser', False)
        
        # Validation
        if cycles < 1 or cycles > 20:
            return jsonify({
                'success': False,
                'message': 'Number of cycles must be between 1 and 20.'
            })
        
        if max_retries < 1 or max_retries > 10:
            return jsonify({
                'success': False,
                'message': 'Max retries must be between 1 and 10.'
            })
        
        if failure_handling not in ['skip', 'retry', 'stop']:
            return jsonify({
                'success': False,
                'message': 'Invalid failure handling option.'
            })
        
        # Create database session
        session_id = create_soak_test_session('tem_cycling', config)
        if not session_id:
            return jsonify({
                'success': False,
                'message': 'Failed to create test session in database.'
            })
        
        # Set global flags
        soak_test_in_progress = True
        soak_test_stop_event.clear()
        
        # Start test in background thread
        current_soak_test_thread = threading.Thread(
            target=soak_test_tem_cycling,
            args=(session_id, config),
            daemon=True
        )
        current_soak_test_thread.start()
        
        print(f"Started TEM Disk Cycling test - Session ID: {session_id}")
        
        return jsonify({
            'success': True,
            'message': 'Test started successfully',
            'session_id': session_id
        })
        
    except Exception as e:
        # Reset flags on error
        soak_test_in_progress = False
        print(f"Error starting TEM cycling test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error starting test: {str(e)}'
        })

@app.route('/soak_tests/tem_cycling/pause', methods=['POST'])
def pause_tem_cycling_test():
    """Pause the current TEM Disk Cycling test."""
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'tem_cycling' or
            current_session.get('status') != 'running'):
            return jsonify({
                'success': False,
                'message': 'No running TEM Disk Cycling test found.'
            })
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'paused',
            'current_step': 'Test paused by user'
        })
        
        print("TEM Disk Cycling test paused")
        return jsonify({
            'success': True,
            'message': 'Test paused successfully'
        })
        
    except Exception as e:
        print(f"Error pausing TEM cycling test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error pausing test: {str(e)}'
        })

@app.route('/soak_tests/tem_cycling/resume', methods=['POST'])
def resume_tem_cycling_test():
    """Resume the paused TEM Disk Cycling test."""
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'tem_cycling' or
            current_session.get('status') != 'paused'):
            return jsonify({
                'success': False,
                'message': 'No paused TEM Disk Cycling test found.'
            })
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'running',
            'current_step': 'Test resumed by user'
        })
        
        print("TEM Disk Cycling test resumed")
        return jsonify({
            'success': True,
            'message': 'Test resumed successfully'
        })
        
    except Exception as e:
        print(f"Error resuming TEM cycling test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error resuming test: {str(e)}'
        })

@app.route('/soak_tests/tem_cycling/stop', methods=['POST'])
def stop_tem_cycling_test():
    """Stop the current TEM Disk Cycling test."""
    global soak_test_in_progress, current_soak_test_session
    
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'tem_cycling'):
            return jsonify({
                'success': False,
                'message': 'No TEM Disk Cycling test found.'
            })
        
        # Signal the test thread to stop
        soak_test_stop_event.set()
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'stopped',
            'end_time': datetime.now(),
            'current_step': 'Test stopped by user',
            'failure_reason': 'User requested stop'
        })
        
        # Reset global flags
        soak_test_in_progress = False
        current_soak_test_session = None
        
        print("TEM Disk Cycling test stopped")
        return jsonify({
            'success': True,
            'message': 'Test stopped successfully'
        })
        
    except Exception as e:
        print(f"Error stopping TEM cycling test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error stopping test: {str(e)}'
        })

@app.route('/soak_tests/sem_to_stage')
def sem_to_stage_test_page():
    """SEM to Stage Transfer soak test page with database integration."""
    try:
        # Get current test session from database
        current_session = get_current_soak_test_session()
        test_session = None
        
        if (current_session and 
            current_session.get('test_type') == 'sem_to_stage'):
            
            # Enhance session data with computed fields
            test_session = current_session.copy()
            
            # Calculate elapsed time
            if test_session.get('start_time'):
                start_time = datetime.fromisoformat(test_session['start_time'])
                elapsed = datetime.now() - start_time
                test_session['elapsed_time_minutes'] = elapsed.total_seconds() / 60
            
            # Calculate estimated completion based on operations completed
            total_transfers = 15  # Fixed number of transfers
            if (test_session.get('total_operations') and 
                test_session.get('elapsed_time_minutes') and
                test_session['total_operations'] > 0):
                
                progress_ratio = test_session['total_operations'] / total_transfers
                if progress_ratio > 0:
                    total_estimated_minutes = test_session['elapsed_time_minutes'] / progress_ratio
                    remaining_minutes = total_estimated_minutes - test_session['elapsed_time_minutes']
                    completion_time = datetime.now() + timedelta(minutes=remaining_minutes)
                    test_session['estimated_completion'] = completion_time.strftime('%H:%M')
            
            # Parse test parameters for display
            if test_session.get('test_parameters'):
                try:
                    params = json.loads(test_session['test_parameters'])
                    test_session['failure_handling'] = params.get('failure_handling', 'skip')
                    test_session['max_retries'] = params.get('max_retries', 3)
                except:
                    pass
        
        # Get recent errors for this test session
        recent_errors = []
        if test_session:
            recent_errors = get_soak_test_errors_for_session(test_session['id'], limit=10)
        
        return render_template('soak_tests/sem_to_stage.html',
                             test_session=test_session,
                             recent_errors=recent_errors)
                             
    except Exception as e:
        print(f"Error loading SEM to Stage test page: {e}")
        return render_template('soak_tests/sem_to_stage.html',
                             test_session=None,
                             recent_errors=[],
                             error_message=str(e))

@app.route('/soak_tests/sem_to_stage/start', methods=['POST'])
def start_sem_to_stage_test():
    """Start a new SEM to Stage Transfer soak test with database integration."""
    global soak_test_in_progress, current_soak_test_session, current_soak_test_thread
    
    try:
        # Check if another test is running
        if soak_test_in_progress or get_current_soak_test_session():
            return jsonify({
                'success': False,
                'message': 'Another soak test is currently running. Please wait for it to complete.'
            })
        
        # Get and validate configuration
        config = request.json
        max_retries = int(config.get('max_retries', 3))
        failure_handling = config.get('failure_handling', 'skip')
        
        # Validation
        if max_retries < 1 or max_retries > 10:
            return jsonify({
                'success': False,
                'message': 'Max retries must be between 1 and 10.'
            })
        
        if failure_handling not in ['skip', 'retry', 'stop']:
            return jsonify({
                'success': False,
                'message': 'Invalid failure handling option.'
            })
        
        # Create database session
        session_id = create_soak_test_session('sem_to_stage', config)
        if not session_id:
            return jsonify({
                'success': False,
                'message': 'Failed to create test session in database.'
            })
        
        # Set global flags
        soak_test_in_progress = True
        soak_test_stop_event.clear()
        
        # Start test in background thread
        current_soak_test_thread = threading.Thread(
            target=soak_test_sem_to_stage,
            args=(session_id, config),
            daemon=True
        )
        current_soak_test_thread.start()
        
        print(f"Started SEM to Stage Transfer test - Session ID: {session_id}")
        
        return jsonify({
            'success': True,
            'message': 'Test started successfully',
            'session_id': session_id
        })
        
    except Exception as e:
        # Reset flags on error
        soak_test_in_progress = False
        print(f"Error starting SEM to Stage test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error starting test: {str(e)}'
        })

@app.route('/soak_tests/sem_to_stage/pause', methods=['POST'])
def pause_sem_to_stage_test():
    """Pause the current SEM to Stage Transfer test."""
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'sem_to_stage' or
            current_session.get('status') != 'running'):
            return jsonify({
                'success': False,
                'message': 'No running SEM to Stage Transfer test found.'
            })
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'paused',
            'current_step': 'Test paused by user'
        })
        
        print("SEM to Stage Transfer test paused")
        return jsonify({
            'success': True,
            'message': 'Test paused successfully'
        })
        
    except Exception as e:
        print(f"Error pausing SEM to Stage test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error pausing test: {str(e)}'
        })

@app.route('/soak_tests/sem_to_stage/resume', methods=['POST'])
def resume_sem_to_stage_test():
    """Resume the paused SEM to Stage Transfer test."""
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'sem_to_stage' or
            current_session.get('status') != 'paused'):
            return jsonify({
                'success': False,
                'message': 'No paused SEM to Stage Transfer test found.'
            })
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'running',
            'current_step': 'Test resumed by user'
        })
        
        print("SEM to Stage Transfer test resumed")
        return jsonify({
            'success': True,
            'message': 'Test resumed successfully'
        })
        
    except Exception as e:
        print(f"Error resuming SEM to Stage test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error resuming test: {str(e)}'
        })

@app.route('/soak_tests/sem_to_stage/stop', methods=['POST'])
def stop_sem_to_stage_test():
    """Stop the current SEM to Stage Transfer test."""
    global soak_test_in_progress, current_soak_test_session
    
    try:
        current_session = get_current_soak_test_session()
        
        if (not current_session or 
            current_session.get('test_type') != 'sem_to_stage'):
            return jsonify({
                'success': False,
                'message': 'No SEM to Stage Transfer test found.'
            })
        
        # Signal the test thread to stop
        soak_test_stop_event.set()
        
        # Update database
        update_soak_test_session(current_session['id'], {
            'status': 'stopped',
            'end_time': datetime.now(),
            'current_step': 'Test stopped by user',
            'failure_reason': 'User requested stop'
        })
        
        # Reset global flags
        soak_test_in_progress = False
        current_soak_test_session = None
        
        print("SEM to Stage Transfer test stopped")
        return jsonify({
            'success': True,
            'message': 'Test stopped successfully'
        })
        
    except Exception as e:
        print(f"Error stopping SEM to Stage test: {e}")
        return jsonify({
            'success': False,
            'message': f'Error stopping test: {str(e)}'
        })

#endregion

def dispatch_action(data):
    """
    Enhanced dispatch_action with proper state tracking.
    This replaces both dispatch_action and original_dispatch_action functions.
    """
    global soak_test_in_progress, current_process_runs
    
    print("Received data:", data)  # debug line
    function_type = data.get('function')
    identifier = data.get('id')
    
    # Check if a soak test is running and block conflicting operations
    if soak_test_in_progress:
        # Define operations that should be blocked during soak tests
        blocked_operations = [
            'sem_process', 'tem_process', 'tem_manual_prepare', 'tem_manual_expose', 
            'tem_manual_complete', 'robot_manual_move', 'robot_manual_home', 
            'device_extend_bed', 'device_retract_bed', 'send_manual_plc_command'
        ]
        
        if function_type in blocked_operations:
            error_msg = ("A soak test is currently running. "
                        "Please stop the soak test before performing other operations. "
                        "Visit the soak test page to manage the running test.")
            print(f"Operation blocked due to soak test: {function_type}")
            socketio.emit('function_response', {'result': error_msg})
            return error_msg
    
    # Get the action function from the function map
    action_function = function_map.get(function_type)
    
    # Determine process type for logging
    process_type = determine_process_type(function_type, data)
    
    # Start process run logging for major operations
    process_run_id = None
    start_time = time.time()
    
    if should_log_process(function_type):
        # UPDATE SYSTEM STATE TO RUNNING
        update_system_state('running', function_type, None)
        
        # Extract parameters for logging
        parameters = extract_parameters_for_logging(function_type, data)
        process_run_id = start_process_run(process_type, parameters)
        
        # Store in global tracking dict
        if process_run_id:
            current_process_runs[process_run_id] = {
                'start_time': start_time,
                'function_type': function_type,
                'process_type': process_type
            }
    
    if action_function is None:
        error_msg = f"Unknown function type: {function_type}"
        if process_run_id:
            log_error(process_run_id, error_msg, "system")
            end_process_run(process_run_id, False, "User_Error", time.time() - start_time)
            # UPDATE SYSTEM STATE BACK TO IDLE WITH ERROR
            update_system_state('idle', function_type, None)
        else:
            log_standalone_error(error_msg, "system")
        return error_msg
    
    try:
        # Execute the function with appropriate parameters
        if function_type == 'button':
            result = action_function(identifier)
            
        elif function_type == 'sem_process':
            result = action_function(
                voltage=data.get('voltage'),
                c_height=data.get('c_height'),
                distance=data.get('distance'),
                etime=data.get('time'),
                origin=data.get('origin'),
                destination=data.get('destination'),
                process_run_id=process_run_id,
                motor1_enabled=data.get('motor1_enabled', False),
                motor2_enabled=data.get('motor2_enabled', False)
            )
            
        elif function_type == 'tem_process':
            result = action_function(
                voltage=data.get('voltage'),
                c_height=data.get('c_height'),
                distance=data.get('distance'),
                etime=data.get('time'),
                origin=data.get('origin'),
                destination=data.get('destination'),
                skip_laser=data.get('skip_laser', False),
                process_run_id=process_run_id,
                motor1_enabled=data.get('motor1_enabled', False),
                motor2_enabled=data.get('motor2_enabled', False)
            )
            
        elif function_type in ['tem_manual_prepare', 'tem_manual_expose', 'tem_manual_complete']:
            if function_type == 'tem_manual_expose':
                result = action_function(
                    voltage=data.get('voltage'),
                    c_height=data.get('c_height'),
                    distance=data.get('distance'),
                    time=data.get('time'),
                    process_run_id=process_run_id,
                    motor1_enabled=data.get('motor1_enabled', False),
                    motor2_enabled=data.get('motor2_enabled', False)
                )
            else:
                result = action_function(process_run_id=process_run_id)
                
        elif function_type == 'robot_manual_move':
            result = action_function(
                x=data.get('x'),
                y=data.get('y'),
                z=data.get('z'),
                c3dp_speed=data.get('c3dp_speed')
            )
            
        elif function_type == 'send_manual_plc_command':
            result = action_function(data.get('command'))
            
        else:
            # For all other functions, call them without extra parameters
            result = action_function()
        
        # Log completion using improved success determination
        if process_run_id and current_process_runs.get(process_run_id):
            success = determine_operation_success(result)  # Use the renamed function
            
            if success:
                end_process_run(process_run_id, True, "Success", time.time() - start_time)
                # UPDATE SYSTEM STATE BACK TO IDLE (SUCCESS)
                update_system_state('idle', function_type, None)
            else:
                end_process_run(process_run_id, False, "Process_Failed", time.time() - start_time)
                log_error(process_run_id, str(result), "process")
                # UPDATE SYSTEM STATE BACK TO IDLE (ERROR WILL BE FOUND VIA QUERY)
                update_system_state('idle', function_type, None)
            
            # Clean up tracking
            current_process_runs.pop(process_run_id, None)
        
        return result
    
    except Exception as e:
        error_msg = f"Error executing {function_type}: {str(e)}"
        print(error_msg)
        
        if process_run_id:
            log_error(process_run_id, error_msg, "application")
            end_process_run(process_run_id, False, "Application_Error", time.time() - start_time)
            current_process_runs.pop(process_run_id, None)
            # UPDATE SYSTEM STATE BACK TO IDLE (ERROR LOGGED)
            update_system_state('idle', function_type, None)
        else:
            log_standalone_error(error_msg, "application")
        
        return error_msg

def determine_operation_success(result):
    """
    Determine if a function result indicates success.
    
    Args:
        result: The result returned by the action function
    
    Returns:
        bool: True if successful, False if failed
    """
    if isinstance(result, bool):
        return result
    elif isinstance(result, str):
        # Check for common error indicators in string results
        error_indicators = ['error', 'failed', 'timeout', 'not picked', 'not detected']
        result_lower = result.lower()
        return not any(indicator in result_lower for indicator in error_indicators)
    else:
        # For other types, assume success unless explicitly False or None
        return result is not False and result is not None

def determine_process_type(function_type, data):
    """Determine the process type for logging purposes."""
    if function_type == 'sem_process':
        return 'sem_process'
    elif function_type == 'tem_process':
        return 'tem_process'
    elif function_type in ['tem_manual_prepare', 'tem_manual_expose', 'tem_manual_complete']:
        return 'TEM_manual'
    elif 'machine_test' in function_type or 'test_connectivity' in function_type:
        return 'machine_test'
    elif 'control_panel' in function_type:
        return 'control_panel'
    elif 'robot' in function_type or 'device' in function_type:
        return 'robot_operation'
    else:
        return 'other'

def should_log_process(function_type):
    """Determine if this function type should be logged as a process run."""
    major_processes = [
        'sem_process', 'tem_process', 'tem_manual_prepare', 
        'tem_manual_expose', 'tem_manual_complete',
        'c3dp_test_connectivity_machine_test_page',
        'robot_manual_move', 'robot_manual_home'
    ]
    return function_type in major_processes

def extract_parameters_for_logging(function_type, data):
    """Extract relevant parameters for logging."""
    if function_type in ['sem_process', 'tem_process', 'tem_manual_expose']:
        return {
            'voltage': data.get('voltage'),
            'c_height': data.get('c_height'),
            'distance': data.get('distance'),
            'time': data.get('time'),
            'origin': data.get('origin'),
            'destination': data.get('destination'),
            'skip_laser': data.get('skip_laser', False) if function_type == 'tem_process' else None
        }
    elif function_type == 'robot_manual_move':
        return {
            'x': data.get('x'),
            'y': data.get('y'),
            'z': data.get('z'),
            'speed': data.get('c3dp_speed')
        }
    else:
        return {key: value for key, value in data.items() if key != 'function'}

def determine_error_category(error_message):
    """Determine error category based on error message content."""
    error_lower = error_message.lower()
    
    if any(keyword in error_lower for keyword in ['robot', '3dp', 'printer', 'serial', 'com port', 'connection']):
        return 'Robot_Communication'
    elif any(keyword in error_lower for keyword in ['plc', 'socket', 'timeout', 'macstat']):
        return 'PLC_Communication'
    elif any(keyword in error_lower for keyword in ['stub not picked', 'grid not picked', 'laser', 'position']):
        return 'Process_Failed'
    elif any(keyword in error_lower for keyword in ['invalid', 'parameter', 'value', 'range']):
        return 'User_Error'
    else:
        return 'System_Error'

def determine_error_component(function_type, error_message):
    """Determine which component the error relates to."""
    error_lower = error_message.lower()
    
    if any(keyword in error_lower for keyword in ['robot', '3dp', 'printer', 'serial']):
        return 'robot'
    elif any(keyword in error_lower for keyword in ['plc', 'socket', 'macstat']):
        return 'PLC'
    elif 'control_panel' in function_type:
        return 'PLC'
    elif 'robot' in function_type or 'device' in function_type:
        return 'robot'
    else:
        return 'system'

# UDP server function to handle commands and respond via UDP and Socket.IO
def udp_server():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        
        try:
            udp_socket.bind((host_ip, udp_port))
            print(f"UDP server listening on {host_ip}:{udp_port}")
        except OSError as e:
            print(f"Failed to bind UDP socket: {e}")
            return
        
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
                
                # Convert result to string if it's not already
                if not isinstance(result, str):
                    result = str(result)
                
                # Send response back through UDP and include an EOF marker
                response = result + "\n"
                udp_socket.sendto(response.encode('utf-8'), addr)

            except Exception as e:
                error_msg = f"Error in UDP server: {str(e)}"
                print(error_msg)
                socketio.emit('function_response', {'result': error_msg}, namespace='/')

# Helper function to parse UDP messages in key=value format
def parse_udp_message(message):
    """Parse UDP messages in key=value format and strip whitespace from values."""
    try:
        # Strip whitespace from the entire message first
        message = message.strip()
        
        # Parse key=value pairs
        params = {}
        for item in message.split("&"):
            if "=" in item:
                key, value = item.split("=", 1)  # Only split on first =
                params[key.strip()] = value.strip()  # Strip whitespace from both key and value
        
        return params
    except ValueError:
        return {}

if __name__ == '__main__':
    # Initialize the application and database
    initialize_app()
    
    # Initialize Socket.IO with engineio_logger for debugging
    socketio = SocketIO(app, logger=True, engineio_logger=True)
    
    # Start the UDP server thread
    udp_thread = threading.Thread(target=udp_server, daemon=True)
    udp_thread.start()
    print(f"UDP server thread started. Listening on {host_ip}:{udp_port}")
    
    # Run the Flask application
    socketio.run(app, host=host_ip, port=web_port, debug=True, allow_unsafe_werkzeug=True)