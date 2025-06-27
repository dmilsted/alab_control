// Enhanced SVG handling for single-page application architecture
// Complete clean version of svg_handling.js

// Global position manager
window.positionManager = {
    positions: {
        sem: {},
        tem: {}
    },
    
    // Track current selections to clear them properly
    currentSelections: {
        semOrigin: null,
        semDestination: null,
        temOrigin: null,
        temDestination: null
    },
    
    // Color scheme for position status
    colorScheme: {
        empty: '#E8E8E8',      // Light gray - no sample holder
        clean: '#90EE90',      // Light green - clean sample available for processing
        occupied: '#FFB6C1',   // Light pink - exposed/used sample present
        selected: '#FFD700',   // Gold - currently selected origin (SEM and TEM clean)
        selectedDestination: '#87CEEB', // Light blue - currently selected destination (TEM)
        unavailable: '#D3D3D3' // Darker gray - position exists but not selectable
    },

    // Fetch current position statuses from server
    async fetchPositions(systemType) {
        try {
            const response = await fetch('/handle_function', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    function: systemType === 'sem' ? 'get_sem_positions' : 'get_tem_positions'
                })
            });
            
            const data = await response.json();
            
            if (data.status === 'success') {
                try {
                    let positions = {};
                    
                    // Check if it's the pipe-separated format
                    if (typeof data.message === 'string' && data.message.includes('|')) {
                        // Parse the pipe-separated format: "SEM|Last change:...|A1:empty|A2:occupied|..."
                        const parts = data.message.split('|');
                        
                        for (let i = 2; i < parts.length; i++) { // Skip first two parts (SEM and timestamp)
                            const [positionName, status] = parts[i].split(':');
                            if (positionName && status) {
                                // Determine if it's a tray or stage position
                                let positionType = 'tray';
                                if (positionName.startsWith('PH_STUB_') || positionName.startsWith('PH_Z')) {
                                    positionType = 'stage';
                                }
                                
                                // Only include actual sample holder positions (skip Z positions)
                                if (!positionName.includes('_Z')) {
                                    positions[positionName] = {
                                        type: positionType,
                                        status: status,
                                        last_updated: new Date().toISOString()
                                    };
                                }
                            }
                        }
                    } else {
                        // Try to parse as JSON (fallback for other formats)
                        const response = JSON.parse(data.message);
                        
                        if (response.type && response.positions) {
                            // Remote monitoring format: {type: "sem_positions", positions: {...}}
                            positions = response.positions;
                        } else {
                            // Direct format: {A1: {type: "tray", status: "clean"}, ...}
                            positions = response;
                        }
                    }
                    
                    this.positions[systemType] = positions;
                    console.log(`${systemType.toUpperCase()} positions loaded:`, positions);
                    return positions;
                } catch (e) {
                    console.error('Error parsing position data:', e, 'Raw data:', data.message);
                    return {};
                }
            } else {
                console.error('Failed to fetch positions:', data.message);
                return {};
            }
        } catch (error) {
            console.error('Error fetching positions:', error);
            return {};
        }
    },

    // Update SVG colors based on position availability
    updateSVGColors(systemType, pageType) {
        const positions = this.positions[systemType];
        console.log(`Updating ${systemType} ${pageType} colors:`, positions);
        
        if (systemType === 'sem') {
            if (pageType === 'tray') {
                this.updateSEMTrayColors(positions);
            } else if (pageType === 'stage') {
                this.updateSEMStageColors(positions);
            }
        } else if (systemType === 'tem') {
            this.updateTEMTrayColors(positions);
        }
    },

    updateSEMTrayColors(positions) {
        console.log('Updating SEM tray colors');
        Object.keys(positions).forEach(positionName => {
            const positionData = positions[positionName];
            
            // Only update tray positions
            if (positionData.type === 'tray') {
                const circle = document.getElementById(positionName);
                if (circle) {
                    const color = this.colorScheme[positionData.status] || this.colorScheme.unavailable;
                    circle.setAttribute('fill', color);
                    console.log(`Updated ${positionName} to ${positionData.status} (${color})`);
                    
                    // Add click handler only for available positions
                    if (positionData.status === 'clean') {
                        circle.style.cursor = 'pointer';
                        circle.onclick = () => this.selectSEMOrigin(positionName);
                    } else {
                        circle.style.cursor = 'not-allowed';
                        circle.onclick = null;
                    }
                } else {
                    console.warn(`Circle element ${positionName} not found`);
                }
            }
        });
    },

    updateSEMStageColors(positions) {
        console.log('Updating SEM stage colors');
        Object.keys(positions).forEach(positionName => {
            const positionData = positions[positionName];
            
            if (positionData.type === 'tray') {
                // Update origin tray circles (semTrayContainer)
                const circle = document.getElementById(positionName);
                if (circle) {
                    const color = this.colorScheme[positionData.status] || this.colorScheme.unavailable;
                    circle.setAttribute('fill', color);
                    
                    if (positionData.status === 'clean') {
                        circle.style.cursor = 'pointer';
                        circle.onclick = () => this.selectSEMOrigin(positionName);
                    } else {
                        circle.style.cursor = 'not-allowed';
                        circle.onclick = null;
                    }
                }
            } else if (positionData.type === 'stage') {
                // Update destination stage positions (stageContainer)
                // Convert PH_STUB_X format to stg_x format for SVG IDs
                let stageId;
                if (positionName.startsWith('PH_STUB_')) {
                    const stageName = positionName.replace('PH_STUB_', '').toLowerCase();
                    stageId = `stg_${stageName}`;
                } else {
                    stageId = positionName; // In case it's already in the right format
                }
                
                const stageElement = document.getElementById(stageId);
                
                if (stageElement) {
                    // Check if this is a permanently forbidden position (hardware limitation)
                    const isPermanentlyForbidden = stageElement.classList.contains('forbidden-position');
                    
                    if (isPermanentlyForbidden) {
                        // Don't change color - keep the red color for hardware-forbidden positions
                        stageElement.style.cursor = 'not-allowed';
                        stageElement.onclick = null;
                        console.log(`Stage ${stageId} (${positionName}) is permanently forbidden - keeping red color`);
                    } else {
                        // This position can change status - apply our dynamic colors
                        stageElement.classList.remove('selectable-position', 'selected');
                        
                        if (positionData.status === 'empty') {
                            // Position is available for placement
                            stageElement.setAttribute('fill', this.colorScheme.empty);
                            stageElement.style.fill = this.colorScheme.empty;
                            stageElement.classList.add('selectable-position');
                            stageElement.style.cursor = 'pointer';
                            stageElement.onclick = () => this.selectSEMDestination(positionName);
                        } else {
                            // Position is occupied - use our pink color
                            stageElement.setAttribute('fill', this.colorScheme.occupied);
                            stageElement.style.fill = this.colorScheme.occupied;
                            stageElement.style.cursor = 'not-allowed';
                            stageElement.onclick = null;
                        }
                        
                        console.log(`Updated stage ${stageId} (${positionName}) to ${positionData.status}`);
                    }
                } else {
                    console.warn(`Stage element ${stageId} (${positionName}) not found`);
                }
            }
        });
    },

    updateTEMTrayColors(positions) {
        console.log('Updating TEM tray colors');
        Object.keys(positions).forEach(positionName => {
            const positionData = positions[positionName];
            const rect = document.getElementById(positionName);
            
            if (rect) {
                const color = this.colorScheme[positionData.status] || this.colorScheme.unavailable;
                rect.setAttribute('fill', color);
                
                // TEM logic: clean positions for origin, empty positions for destination
                if ((positionData.status === 'clean' && positionName.startsWith('TC')) ||
                    (positionData.status === 'empty' && positionName.startsWith('TE'))) {
                    rect.style.cursor = 'pointer';
                    rect.onclick = () => this.selectTEMPosition(positionName);
                } else {
                    rect.style.cursor = 'not-allowed';
                    rect.onclick = null;
                }
            }
        });
    },

    // Selection handlers
    selectSEMOrigin(positionName) {
        const originSelect = document.getElementById('origin');
        if (originSelect) {
            originSelect.value = positionName;
            // Call our enhanced function to handle the selection
            window.enhancedSEMOriginChange(originSelect);
        }
    },

    selectSEMDestination(positionName) {
        const destinationSelect = document.getElementById('destination');
        if (destinationSelect) {
            destinationSelect.value = positionName;
            // Call our enhanced function to handle the selection
            window.enhancedSEMDestinationChange(destinationSelect);
        }
    },

    selectTEMPosition(positionName) {
        if (positionName.startsWith('TC')) {
            const originSelect = document.getElementById('origin');
            if (originSelect) {
                originSelect.value = positionName;
                window.enhancedTEMOriginChange(originSelect);
            }
        } else if (positionName.startsWith('TE')) {
            const destinationSelect = document.getElementById('destination');
            if (destinationSelect) {
                destinationSelect.value = positionName;
                window.enhancedTEMDestinationChange(destinationSelect);
            }
        }
    },

    // Update dropdown options based on availability
    updateDropdownOptions(systemType, pageType) {
        const positions = this.positions[systemType];
        
        if (pageType === 'tray' && systemType === 'sem') {
            const originSelect = document.getElementById('origin');
            if (originSelect) {
                // Enable/disable options based on availability
                Array.from(originSelect.options).forEach(option => {
                    if (option.value && positions[option.value]) {
                        const positionData = positions[option.value];
                        if (positionData.type === 'tray') {
                            if (positionData.status === 'clean') {
                                option.disabled = false;
                                option.style.color = 'black';
                            } else {
                                option.disabled = true;
                                option.style.color = 'gray';
                                option.title = `Position ${option.value} is ${positionData.status} - not available for selection`;
                            }
                        }
                    }
                });
            }
        } else if (pageType === 'stage' && systemType === 'sem') {
            // Update both origin and destination dropdowns for stage page
            const originSelect = document.getElementById('origin');
            const destinationSelect = document.getElementById('destination');
            
            if (originSelect) {
                // Enable/disable origin options (tray positions)
                Array.from(originSelect.options).forEach(option => {
                    if (option.value && positions[option.value]) {
                        const positionData = positions[option.value];
                        if (positionData.type === 'tray') {
                            if (positionData.status === 'clean') {
                                option.disabled = false;
                                option.style.color = 'black';
                            } else {
                                option.disabled = true;
                                option.style.color = 'gray';
                                option.title = `Position ${option.value} is ${positionData.status} - not available for pickup`;
                            }
                        }
                    }
                });
            }
            
            if (destinationSelect) {
                // Enable/disable destination options (stage positions)
                Array.from(destinationSelect.options).forEach(option => {
                    if (option.value) {
                        // Check if position exists in database
                        if (positions[option.value]) {
                            const positionData = positions[option.value];
                            if (positionData.type === 'stage') {
                                if (positionData.status === 'empty') {
                                    option.disabled = false;
                                    option.style.color = 'black';
                                } else {
                                    option.disabled = true;
                                    option.style.color = 'gray';
                                    option.title = `Position ${option.value} is ${positionData.status} - not available for placement`;
                                }
                            }
                        } else {
                            // Position not in database - check if it's permanently forbidden in HTML
                            const positionValue = option.value.replace('PH_STUB_', '').toLowerCase();
                            const stageId = `stg_${positionValue}`;
                            const stageElement = document.getElementById(stageId);
                            
                            if (stageElement && stageElement.classList.contains('forbidden-position')) {
                                // Permanently forbidden position
                                option.disabled = true;
                                option.style.color = 'red';
                                option.title = `Position ${option.value} is permanently unavailable - hardware limitation`;
                            }
                        }
                    }
                });
            }
        }
    }
};

// Page initialization function that gets called after content loads
window.initializePositionSystem = async function() {
    console.log('Initializing position system...');
    
    // Wait a bit for DOM to be ready
    setTimeout(async () => {
        // Detect page type by looking for specific elements
        if (document.querySelector('circle[id^="A"]') && !document.getElementById('stageContainer')) {
            console.log('Detected SEM Tray page');
            await window.positionManager.fetchPositions('sem');
            window.positionManager.updateSVGColors('sem', 'tray');
            window.positionManager.updateDropdownOptions('sem', 'tray');
            
        } else if (document.getElementById('stageContainer') && document.querySelector('circle[id^="stg_"]')) {
            console.log('Detected SEM Stage page');
            await window.positionManager.fetchPositions('sem');
            window.positionManager.updateSVGColors('sem', 'stage');
            window.positionManager.updateDropdownOptions('sem', 'stage');
            
        } else if (document.querySelector('rect[id^="TC"]')) {
            console.log('Detected TEM Tray page');
            await window.positionManager.fetchPositions('tem');
            window.positionManager.updateSVGColors('tem', 'tray');
            
        } else {
            console.log('No position system detected on this page');
        }
    }, 500);
    
    // Add event listeners to dropdowns since we removed onchange from HTML
    setTimeout(() => {
        const originSelect = document.getElementById('origin');
        const destinationSelect = document.getElementById('destination');
        
        if (originSelect) {
            originSelect.addEventListener('change', function() {
                console.log('Origin dropdown changed to:', this.value);
                
                // Determine which page we're on and call appropriate function
                if (document.getElementById('stageContainer')) {
                    // SEM Stage page - origin tray selection
                    // First clear the previous selection if it exists
                    if (window.positionManager.currentSelections.semOrigin) {
                        const prevElement = document.getElementById(window.positionManager.currentSelections.semOrigin);
                        if (prevElement) {
                            prevElement.setAttribute('fill', 'white');
                            prevElement.style.fill = 'white';
                        }
                    }
                    
                    // Then apply status colors
                    if (window.positionManager.positions.sem) {
                        window.positionManager.updateSVGColors('sem', 'stage');
                    }
                    
                    // Finally highlight the selection
                    if (this.value) {
                        const selectedCircle = document.getElementById(this.value);
                        if (selectedCircle) {
                            selectedCircle.setAttribute('fill', window.positionManager.colorScheme.selected);
                            selectedCircle.style.fill = window.positionManager.colorScheme.selected;
                        }
                        // Store the current selection
                        window.positionManager.currentSelections.semOrigin = this.value;
                    } else {
                        window.positionManager.currentSelections.semOrigin = null;
                    }
                } else if (document.querySelector('rect[id^="TC"]')) {
                    // TEM Tray page
                    window.enhancedTEMOriginChange(this);
                } else if (document.querySelector('circle[id^="A"]')) {
                    // SEM Tray page
                    window.enhancedSEMOriginChange(this);
                }
            });
        }
        
        if (destinationSelect) {
            destinationSelect.addEventListener('change', function() {
                console.log('Destination dropdown changed to:', this.value);
                
                if (document.getElementById('stageContainer')) {
                    // SEM Stage page - destination stage selection
                    window.enhancedSEMDestinationChange(this);
                } else if (document.querySelector('rect[id^="TC"]')) {
                    // TEM page
                    window.enhancedTEMDestinationChange(this);
                }
            });
        }
    }, 600);
};

// Manual refresh function
window.refreshPositions = async function() {
    console.log('Manual position refresh triggered');
    
    if (document.querySelector('circle[id^="A"]') && !document.getElementById('stageContainer')) {
        await window.positionManager.fetchPositions('sem');
        window.positionManager.updateSVGColors('sem', 'tray');
    } else if (document.getElementById('stageContainer')) {
        await window.positionManager.fetchPositions('sem');
        window.positionManager.updateSVGColors('sem', 'stage');
    } else if (document.querySelector('rect[id^="TC"]')) {
        await window.positionManager.fetchPositions('tem');
        window.positionManager.updateSVGColors('tem', 'tray');
    }
};

// Enhanced dropdown change handlers - these are now the ONLY functions!
window.enhancedSEMOriginChange = function(selectElement) {
    console.log('enhancedSEMOriginChange called with:', selectElement.value);
    const selectedValue = selectElement.value;
    
    // First, clear the previous selection if it exists
    if (window.positionManager.currentSelections.semOrigin) {
        const prevElement = document.getElementById(window.positionManager.currentSelections.semOrigin);
        if (prevElement) {
            prevElement.setAttribute('fill', 'white');
            prevElement.style.fill = 'white';
        }
    }
    
    // Apply our position-based colors to restore status colors
    if (window.positionManager.positions.sem) {
        window.positionManager.updateSVGColors('sem', 'tray');
    }
    
    // Highlight the new selected position with our gold color
    if (selectedValue) {
        const selectedCircle = document.getElementById(selectedValue);
        if (selectedCircle) {
            selectedCircle.setAttribute('fill', window.positionManager.colorScheme.selected);
            selectedCircle.style.fill = window.positionManager.colorScheme.selected;
            console.log('Highlighted', selectedValue, 'with gold color');
        }
        // Store the current selection
        window.positionManager.currentSelections.semOrigin = selectedValue;
    } else {
        window.positionManager.currentSelections.semOrigin = null;
    }
    
    // Update hidden destination field if it exists (for SEM tray page)
    const destinationField = document.getElementById("destination");
    if (destinationField && destinationField.type === 'hidden') {
        destinationField.value = selectedValue;
    }
};

window.enhancedSEMDestinationChange = function(selectElement) {
    console.log('enhancedSEMDestinationChange called with:', selectElement.value);
    const selectedValue = selectElement.value;
    
    // First, clear the previous stage selection if it exists
    if (window.positionManager.currentSelections.semDestination) {
        const prevStageId = window.positionManager.currentSelections.semDestination.startsWith('PH_STUB_') 
            ? `stg_${window.positionManager.currentSelections.semDestination.replace('PH_STUB_', '').toLowerCase()}`
            : window.positionManager.currentSelections.semDestination;
        const prevElement = document.getElementById(prevStageId);
        if (prevElement && !prevElement.classList.contains('forbidden-position')) {
            prevElement.setAttribute('fill', 'white');
            prevElement.style.fill = 'white';
        }
    }
    
    // Apply position-based colors to restore status colors
    if (window.positionManager.positions.sem) {
        window.positionManager.updateSVGColors('sem', 'stage');
    }
    
    // Highlight the new selected stage position with our gold color
    if (selectedValue) {
        // Convert PH_STUB_X to stg_x format
        let stageId;
        if (selectedValue.startsWith('PH_STUB_')) {
            const stageName = selectedValue.replace('PH_STUB_', '').toLowerCase();
            stageId = `stg_${stageName}`;
        } else {
            stageId = selectedValue;
        }
        
        const selectedElement = document.getElementById(stageId);
        if (selectedElement) {
            // Check if it's permanently forbidden
            const isPermanentlyForbidden = selectedElement.classList.contains('forbidden-position');
            
            if (!isPermanentlyForbidden) {
                selectedElement.style.fill = window.positionManager.colorScheme.selected;
                selectedElement.setAttribute('fill', window.positionManager.colorScheme.selected);
                console.log('Highlighted stage', stageId, 'with gold color');
            } else {
                alert("This position is not available for selection - hardware limitation.");
                return;
            }
        }
        // Store the current selection
        window.positionManager.currentSelections.semDestination = selectedValue;
    } else {
        window.positionManager.currentSelections.semDestination = null;
    }
};

window.enhancedTEMOriginChange = function(selectElement) {
    console.log('enhancedTEMOriginChange called with:', selectElement.value);
    const selectedValue = selectElement.value;
    
    // First, clear the previous origin selection if it exists
    if (window.positionManager.currentSelections.temOrigin) {
        const prevElement = document.getElementById(window.positionManager.currentSelections.temOrigin);
        if (prevElement) {
            prevElement.setAttribute('fill', 'white');
            prevElement.style.fill = 'white';
        }
    }
    
    // Apply position-based colors to restore status colors
    if (window.positionManager.positions.tem) {
        window.positionManager.updateSVGColors('tem', 'tray');
    }
    
    // Highlight selected origin with GOLD (for clean TC positions)
    if (selectedValue) {
        const selectedRect = document.getElementById(selectedValue);
        if (selectedRect) {
            selectedRect.setAttribute('fill', window.positionManager.colorScheme.selected);
            selectedRect.style.fill = window.positionManager.colorScheme.selected;
            console.log('Highlighted TEM origin', selectedValue, 'with gold color');
        }
        // Store the current selection
        window.positionManager.currentSelections.temOrigin = selectedValue;
    } else {
        window.positionManager.currentSelections.temOrigin = null;
    }
};

window.enhancedTEMDestinationChange = function(selectElement) {
    console.log('enhancedTEMDestinationChange called with:', selectElement.value);
    const selectedValue = selectElement.value;
    
    // First, clear the previous destination selection if it exists
    if (window.positionManager.currentSelections.temDestination) {
        const prevElement = document.getElementById(window.positionManager.currentSelections.temDestination);
        if (prevElement) {
            prevElement.setAttribute('fill', 'white');
            prevElement.style.fill = 'white';
        }
    }
    
    // Apply position-based colors to restore status colors
    if (window.positionManager.positions.tem) {
        window.positionManager.updateSVGColors('tem', 'tray');
    }
    
    // Highlight selected destination with LIGHT BLUE (for TE positions)
    if (selectedValue) {
        const selectedRect = document.getElementById(selectedValue);
        if (selectedRect) {
            selectedRect.setAttribute('fill', window.positionManager.colorScheme.selectedDestination);
            selectedRect.style.fill = window.positionManager.colorScheme.selectedDestination;
            console.log('Highlighted TEM destination', selectedValue, 'with light blue color');
        }
        // Store the current selection
        window.positionManager.currentSelections.temDestination = selectedValue;
    } else {
        window.positionManager.currentSelections.temDestination = null;
    }
};