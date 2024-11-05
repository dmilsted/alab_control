// Terminal functions
function addToTerminal(message) {
    console.log("Adding to terminal:", message);  // Debug log
    const terminalOutput = document.getElementById('terminal-output');
    const line = document.createElement('div');
    line.textContent = `> ${message}`;
    terminalOutput.appendChild(line);
    terminalOutput.scrollTop = terminalOutput.scrollHeight;
}

document.addEventListener('DOMContentLoaded', function() {
    const socket = io();
    const terminalToggle = document.getElementById('terminal-toggle');
    const terminalContainer = document.querySelector('.terminal-container');
    const pageContent = document.getElementById('page-content');

    // Terminal Toggle
    terminalToggle.addEventListener('click', () => {
        terminalContainer.classList.toggle('collapsed');
        terminalToggle.textContent = terminalContainer.classList.contains('collapsed') 
            ? 'View Terminal' 
            : 'Hide Terminal';
    });

    // Socket.IO handlers
    socket.on('connect', () => {
        console.log('Socket.IO connected');
        addToTerminal('Socket.IO connected');
    });

    socket.on('disconnect', () => {
        console.log('Socket.IO disconnected');
        addToTerminal('Socket.IO disconnected');
    });

    socket.on('function_response', function(data) {
        console.log("Received function_response:", data);  // Debug log
        addToTerminal(data.result);
    });

    socket.on('connect_error', (error) => {
        console.error('Socket.IO connect_error:', error);
        addToTerminal(`Socket.IO connect_error: ${error}`);
    });

    socket.on('connect_timeout', (timeout) => {
        console.error('Socket.IO connect_timeout:', timeout);
        addToTerminal(`Socket.IO connect_timeout: ${timeout}`);
    });

    socket.on('error', (error) => {
        console.error('Socket.IO error:', error);
        addToTerminal(`Socket.IO error: ${error}`);
    });

    socket.on('reconnect', (attempt) => {
        console.log('Socket.IO reconnect:', attempt);
        addToTerminal(`Socket.IO reconnect: ${attempt}`);
    });

    socket.on('reconnect_attempt', (attempt) => {
        console.log('Socket.IO reconnect_attempt:', attempt);
        addToTerminal(`Socket.IO reconnect_attempt: ${attempt}`);
    });

    socket.on('reconnect_error', (error) => {
        console.error('Socket.IO reconnect_error:', error);
        addToTerminal(`Socket.IO reconnect_error: ${error}`);
    });

    socket.on('reconnect_failed', () => {
        console.error('Socket.IO reconnect_failed');
        addToTerminal('Socket.IO reconnect_failed');
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
                addToTerminal(`Error loading page: ${error}`);
            });
    }

    // Initialize page handlers
    function initializePageHandlers() {
        // Add event listeners for any forms or buttons on the loaded page
        document.querySelectorAll('[data-function]').forEach(element => {
            element.addEventListener('click', function(e) {
                e.preventDefault();
                const functionData = {
                    function: this.dataset.function,
                    id: this.dataset.id
                };
                callFunction(functionData);
            });
        });
    }

    // Function to call server
    function callFunction(data) {
        let httpMessage = "";
    
        if (data.function === 'button') {
            httpMessage = `HTTP request: ${data.id} clicked`;
        } else { // other cases
            httpMessage = `HTTP request: ${data.function} called`;
        }
    
        addToTerminal(httpMessage);  // Add the HTTP request message directly
    
        fetch('/handle_function', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        })
        .then(response => response.json())
        .then(data => {
            // Only display success message from the server response
            addToTerminal(`Success: ${data.message}`);
        })
        .catch(error => {
            console.error('Error:', error);
            addToTerminal(`Error: ${error}`);
        });
    }

    // Load home page by default
    loadPage('home');
});

function submitForm(form) {
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
        console.log('Success:', data);
        addToTerminal(data.message);
    })
    .catch(error => {
        console.error('Error:', error);
        addToTerminal(`Error: ${error}`);
    });
}