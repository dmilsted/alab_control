window.initializePLCControls = function() {
    // Get main elements
    const commandInput = document.getElementById('plc-command-input');
    if (!commandInput) {
        // We're not on the right page, exit early
        return;
    }

    const commandSelect = document.getElementById('plc-command-select');
    const dynamicControls = document.getElementById('plc-dynamic-controls');
    const sendButton = document.getElementById('plc-send-btn');

    // Command mappings
    const INITIAL_COMMANDS = {
        'standby': 'STANDBY',
        'stage-lid': 'PHLIDMVL',
        'tem-prep': 'TEMPREP',
        'tem-storage': 'TEMSTOR',
        'sem-prep': 'SEMPREP',
        'sem-storage': 'SEMSTOR',
        'hvps': 'EXPOSUR',
        'vibration': 'EXPOSUR',
        'shutdown': 'SHUTDWN'
    };

    // Send command function
    function sendCommand() {
        const command = commandInput.value;
        if (command) {
            // Use Socket.IO instead of callFunction
            socket.emit('execute_function', {
                function_name: 'plc_send_command',
                parameters: [command]
            });
            commandInput.value = ''; // Clear input after sending
        }
    }

    // Append text to command input
    function appendToCommand(text) {
        const currentText = commandInput.value;
        commandInput.value = currentText + text;
    }

    // Create numeric input with validation
    function createNumericInput(placeholder, prefix) {
        const container = document.createElement('div');
        container.className = 'input-group';
    
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = placeholder;
        input.maxLength = 3;
        input.style.width = '220px';
        
        const okButton = document.createElement('button');
        okButton.className = 'btn btn-primary';
        okButton.textContent = 'OK';
        
        okButton.addEventListener('click', () => {
            const value = input.value.padStart(3, '0');
            if (/^\d{3}$/.test(value) && parseInt(value) <= 255) {
                appendToCommand(prefix + value);
                input.value = '';
            } else {
                alert('Please enter a valid number between 000 and 255');
            }
        });

        container.appendChild(input);
        container.appendChild(okButton);
        return container;
    }

    // Handle dropdown changes
    commandSelect.addEventListener('change', function() {
        const selectedValue = this.value;
        dynamicControls.innerHTML = ''; // Clear previous controls
        
        if (selectedValue && INITIAL_COMMANDS[selectedValue]) {
            commandInput.value = INITIAL_COMMANDS[selectedValue];
        }

        switch(selectedValue) {
            case 'stage-lid':
                dynamicControls.appendChild(createNumericInput('Stage value - 000 to 255', ''));
                break;

            case 'tem-prep':
                const vacButton1 = document.createElement('button');
                vacButton1.className = 'btn btn-primary';
                vacButton1.textContent = 'Vacuum ON';
                vacButton1.onclick = () => appendToCommand('VAC1');
                dynamicControls.appendChild(vacButton1);
                dynamicControls.appendChild(createNumericInput('Lid value - 000 to 255', 'L'));
                break;

            case 'tem-storage':
                const vacButton2 = document.createElement('button');
                vacButton2.className = 'btn btn-primary';
                vacButton2.textContent = 'Vacuum OFF';
                vacButton2.onclick = () => appendToCommand('VAC0');
                dynamicControls.appendChild(vacButton2);
                break;

            case 'sem-prep':
                const vacButton3 = document.createElement('button');
                vacButton3.className = 'btn btn-primary';
                vacButton3.textContent = 'Vacuum ON';
                vacButton3.onclick = () => appendToCommand('VAC1');
                dynamicControls.appendChild(vacButton3);
                dynamicControls.appendChild(createNumericInput('Rotator value - 000 to 255', 'R'));
                break;

            case 'sem-storage':
                const vacButton4 = document.createElement('button');
                vacButton4.className = 'btn btn-primary';
                vacButton4.textContent = 'Vacuum OFF';
                vacButton4.onclick = () => appendToCommand('VAC0');
                dynamicControls.appendChild(vacButton4);
                dynamicControls.appendChild(createNumericInput('Gripper value - 000 to 255', 'G'));
                break;

            case 'hvps':
                const hvpsContainer = document.createElement('div');
                hvpsContainer.className = 'input-group';
                
                const voltageInput = document.createElement('input');
                voltageInput.type = 'text';
                voltageInput.placeholder = 'Voltage';
                voltageInput.style.width = '220px';
                
                const timeInput = document.createElement('input');
                timeInput.type = 'text';
                timeInput.placeholder = 'Time';
                timeInput.style.width = '220px';
                
                const okButton = document.createElement('button');
                okButton.className = 'btn btn-primary';
                okButton.textContent = 'OK';
                okButton.onclick = () => {
                    appendToCommand('V' + voltageInput.value + 'T' + timeInput.value);
                };

                hvpsContainer.appendChild(voltageInput);
                hvpsContainer.appendChild(timeInput);
                hvpsContainer.appendChild(okButton);
                dynamicControls.appendChild(hvpsContainer);
                break;

            case 'vibration':
                const vibrationButtons = [
                    {text: 'M1-ON', command: 'M11'},
                    {text: 'M1-OFF', command: 'M10'},
                    {text: 'M2-ON', command: 'M21'},
                    {text: 'M2-OFF', command: 'M20'},
                    {text: 'M3-ON', command: 'M31'},
                    {text: 'M3-OFF', command: 'M30'},
                    {text: 'ALL OFF', command: 'M00'}
                ];

                vibrationButtons.forEach(btn => {
                    const button = document.createElement('button');
                    button.className = 'btn btn-primary';
                    button.textContent = btn.text;
                    button.style.margin = '5px';
                    button.onclick = () => appendToCommand(btn.command);
                    dynamicControls.appendChild(button);
                });
                break;
        }
        
    });

    commandSelect.addEventListener('click', function() {
        const selectedValue = this.value;
        if (selectedValue && INITIAL_COMMANDS[selectedValue]) {
            commandInput.value = INITIAL_COMMANDS[selectedValue];
        }
    });

    // Add send button handler
    sendButton.addEventListener('click', sendCommand);
};