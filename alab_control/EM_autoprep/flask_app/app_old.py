from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import socket
import threading

app = Flask(__name__)
socketio = SocketIO(app)

host_ip = "localhost"  # Set to listen on all interfaces
web_port = 8000
udp_port = 8001

# Define action functions
def button_action(button_id):
    print(f"Button action called for {button_id}")
    return f"Button action performed for {button_id}"

def process_action(voltage, distance, time, origin, destination):
    print(f"Process action with voltage={voltage}, distance={distance}, time={time}, origin={origin}, destination={destination}")
    return f"Processed SEM with voltage={voltage}, distance={distance}, time={time}, origin={origin}, destination={destination}"

# Map function names to handlers
function_map = {
    'button': button_action,
    'process': process_action
}

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
    socketio.emit('function_response', {'result': result})  # Emit to all clients
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
        elif function_type == 'process':
            return action_function(
                voltage=data.get('voltage'),
                distance=data.get('distance'),
                time=data.get('time'),
                origin=data.get('origin'),
                destination=data.get('destination')
            )
        else:
            return "Function type not supported"
    except Exception as e:
        return f"Error: {str(e)}"

# UDP server function to handle commands and respond via UDP and Socket.IO
def udp_server():
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind((host_ip, udp_port))
    print(f"UDP server listening on {host_ip}:{udp_port}")

    while True:
        try:
            data, addr = udp_socket.recvfrom(1024)
            message = data.decode('utf-8')
            print(f"Received UDP command: {message} from {addr}")

            # Parse the UDP message into dictionary format
            params = parse_udp_message(message)
            if 'function' not in params:
                error_message = f"Invalid message format: {message}"
                print(error_message)
                socketio.emit('function_response', {'result': error_message})
                udp_socket.sendto(error_message.encode('utf-8'), addr)
                continue

            # Dispatch action and get result
            result = dispatch_action(params)
            print("Processing result:", result)  # Debug print

            # Emit to web clients
            socketio.emit('function_response', {'result': result})

            # Send response back to UDP client
            udp_socket.sendto(result.encode('utf-8'), addr)
        
        except Exception as e:
            error_message = f"Error processing UDP message: {e}"
            print(error_message)
            socketio.emit('function_response', {'result': error_message})

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
    socketio.run(app, host=host_ip, port=web_port, debug=True)
