window.socket = io();

// Global functions
window.callFunction = function(data) {
    let httpMessage = "";
    if (data.function === 'button') {
        httpMessage = `HTTP request: ${data.id} clicked`;
    } else {
        httpMessage = `HTTP request: ${data.function} called`;
    }
    
    window.addToTerminal(httpMessage);
    
    fetch('/handle_function', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    })
    .then(response => response.json())
    .then(data => {
        window.addToTerminal(`Response: ${data.message}`);
    })
    .catch(error => {
        console.error('Error:', error);
        window.addToTerminal(`Error: ${error}`);
    });
};

window.addToTerminal = function(message) {
    console.log("Adding to terminal:", message);
    const terminalOutput = document.getElementById('terminal-output');
    if (terminalOutput) {
        const line = document.createElement('div');
        line.textContent = `> ${message}`;
        terminalOutput.appendChild(line);
        terminalOutput.scrollTop = terminalOutput.scrollHeight;
    }
};

window.submitForm = function(form) {
    const formData = new FormData(form);
    const data = Object.fromEntries(formData.entries());
    
    fetch('/handle_function', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    })
    .then(response => response.json())
    .then(data => {
        console.log('Response:', data);
        window.addToTerminal(data.message);
    })
    .catch(error => {
        console.error('Error:', error);
        window.addToTerminal(`Error: ${error}`);
    });
};

// Main initialization
document.addEventListener('DOMContentLoaded', function() {
    const socket = io();
    const terminalToggle = document.getElementById('terminal-toggle');
    const terminalContainer = document.querySelector('.terminal-container');
    const pageContent = document.getElementById('page-content');

    // Terminal Toggle
    if (terminalToggle && terminalContainer) {
        terminalToggle.addEventListener('click', () => {
            terminalContainer.classList.toggle('collapsed');
            terminalToggle.textContent = terminalContainer.classList.contains('collapsed') 
                ? 'View Terminal' 
                : 'Hide Terminal';
        });
    }

    // Socket.IO handlers
    socket.on('connect', () => {
        console.log('Socket.IO connected');
        window.addToTerminal('Socket.IO connected');
    });

    socket.on('disconnect', () => {
        console.log('Socket.IO disconnected');
        window.addToTerminal('Socket.IO disconnected');
    });

    socket.on('function_response', function(data) {
        console.log("Received function_response:", data);
        window.addToTerminal(data.result);
    });

    socket.on('connect_error', (error) => {
        console.error('Socket.IO connect_error:', error);
        window.addToTerminal(`Socket.IO connect_error: ${error}`);
    });

    socket.on('connect_timeout', (timeout) => {
        console.error('Socket.IO connect_timeout:', timeout);
        window.addToTerminal(`Socket.IO connect_timeout: ${timeout}`);
    });

    socket.on('error', (error) => {
        console.error('Socket.IO error:', error);
        window.addToTerminal(`Socket.IO error: ${error}`);
    });

    socket.on('reconnect', (attempt) => {
        console.log('Socket.IO reconnect:', attempt);
        window.addToTerminal(`Socket.IO reconnect: ${attempt}`);
    });

    socket.on('reconnect_attempt', (attempt) => {
        console.log('Socket.IO reconnect_attempt:', attempt);
        window.addToTerminal(`Socket.IO reconnect_attempt: ${attempt}`);
    });

    socket.on('reconnect_error', (error) => {
        console.error('Socket.IO reconnect_error:', error);
        window.addToTerminal(`Socket.IO reconnect_error: ${error}`);
    });

    socket.on('reconnect_failed', () => {
        console.error('Socket.IO reconnect_failed');
        window.addToTerminal('Socket.IO reconnect_failed');
    });

    // Navigation handling
    document.querySelectorAll('.nav-btn:not(.terminal-toggle)').forEach(button => {
        button.addEventListener('click', () => {
            const page = button.dataset.page;
            loadPage(page);
        });
    });

    // Load page content
    function loadPage(page) {
        fetch(`/get_page/${page}`)
            .then(response => response.text())
            .then(html => {
                pageContent.innerHTML = html;
                initializePageHandlers();
            })
            .catch(error => {
                console.error('Error loading page:', error);
                window.addToTerminal(`Error loading page: ${error}`);
            });
    }

    // Initialize page handlers
    function initializePageHandlers() {
        document.querySelectorAll('[data-function]').forEach(element => {
            element.addEventListener('click', function(e) {
                e.preventDefault();
                let functionData = {
                    function: this.dataset.function
                };
    
                // Special handling for PLC command
                if (this.dataset.function === 'send_manual_plc_command') {
                    const commandInput = document.getElementById('plc-command-input');
                    if (commandInput) {
                        functionData.command = commandInput.value;
                        functionData.dummydata = 'Lufalufa';
                    }
                }
    
                window.callFunction(functionData);
            });
        });
        initializeMaintenanceControls();
        if (typeof window.initializePLCControls === 'function') {
            window.initializePLCControls();
        }
    }

    // Functions to manually control the 3DP 
    function initializeMaintenanceControls() {
        const moveBtn = document.getElementById('manual-move-btn');
        const homeBtn = document.getElementById('manual-home-btn');

        if (moveBtn) {
            moveBtn.addEventListener('click', function() {
                const x = document.getElementById('x-coord').value;
                const y = document.getElementById('y-coord').value;
                const z = document.getElementById('z-coord').value;
                const c3dp_speed = document.getElementById('3dp-manual-speed').value;
                
                const data = {
                    function: 'robot_manual_move',
                    x: x,
                    y: y,
                    z: z,
                    c3dp_speed: c3dp_speed
                };
                
                window.callFunction(data);
            });
        }

        if (homeBtn) {
            homeBtn.addEventListener('click', function() {
                const data = {
                    function: 'robot_manual_home'
                };
                window.callFunction(data);
            });
        }
    }

    

    // Load home page by default
    loadPage('home');
});