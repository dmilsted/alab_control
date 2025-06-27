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
                    
                    // Check if it's the pipe-separated format like: "SEM|Last change:2025-06-27_03:37|A1:empty|A2:occupied|..."
                    if (typeof data.message === 'string' && data.message.includes('|')) {
                        console.log('Parsing pipe-separated format:', data.message);
                        
                        // Parse the pipe-separated format
                        const parts = data.message.split('|');
                        
                        for (let i = 2; i < parts.length; i++) { // Skip first two parts (SEM/TEM and timestamp)
                            const positionPart = parts[i];
                            if (positionPart && positionPart.includes(':')) {
                                const [positionName, status] = positionPart.split(':');
                                if (positionName && status) {
                                    // Determine if it's a tray or stage position
                                    let positionType = 'tray';
                                    if (positionName.startsWith('PH_STUB_') || positionName.startsWith('PH_Z')) {
                                        positionType = 'stage';
                                    }
                                    
                                    // Only include actual sample holder positions (skip Z positions and STRAY positions)
                                    if (!positionName.includes('_Z') && !positionName.startsWith('STRAY_')) {
                                        positions[positionName] = {
                                            type: positionType,
                                            status: status,
                                            last_updated: new Date().toISOString()
                                        };
                                        console.log(`Parsed position: ${positionName} = ${status} (${positionType})`);
                                    }
                                }
                            }
                        }
                    } else if (typeof data.message === 'string') {
                        // Try to parse as JSON (fallback for other formats)
                        try {
                            const response = JSON.parse(data.message);
                            
                            if (response.type && response.positions) {
                                // Remote monitoring format: {type: "sem_positions", positions: {...}}
                                positions = response.positions;
                            } else {
                                // Direct format: {A1: {type: "tray", status: "clean"}, ...}
                                positions = response;
                            }
                        } catch (jsonError) {
                            console.error('Failed to parse as JSON:', jsonError);
                            return {};
                        }
                    } else {
                        // If it's already an object, handle it directly
                        if (data.message && typeof data.message === 'object') {
                            positions = data.message;
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
        console.log('Updating SEM tray colors with currentSelections:', this.currentSelections);
        Object.keys(positions).forEach(positionName => {
            const positionData = positions[positionName];
            
            // Only update tray positions
            if (positionData.type === 'tray') {
                const circle = document.getElementById(positionName);
                if (circle) {
                    // Skip only if this is the CURRENT selection (after we update currentSelections)
                    if (positionName === this.currentSelections.semOrigin) {
                        console.log(`Skipping color update for currently selected position: ${positionName}`);
                        return;
                    }
                    
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
        console.log('Updating SEM stage colors with currentSelections:', this.currentSelections);
        Object.keys(positions).forEach(positionName => {
            const positionData = positions[positionName];
            
            if (positionData.type === 'tray') {
                // Update origin tray circles (semTrayContainer)
                const circle = document.getElementById(positionName);
                if (circle) {
                    // Skip only if this is the CURRENT selection (after we update currentSelections)
                    if (positionName === this.currentSelections.semOrigin) {
                        console.log(`Skipping color update for currently selected origin: ${positionName}`);
                        return;
                    }
                    
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
                let stageId;
                if (positionName.startsWith('PH_STUB_')) {
                    const stageName = positionName.replace('PH_STUB_', '').toLowerCase();
                    stageId = `stg_${stageName}`;
                } else {
                    stageId = positionName;
                }
                
                const stageElement = document.getElementById(stageId);
                
                if (stageElement) {
                    // Skip only if this is the CURRENT selection (after we update currentSelections)
                    if (positionName === this.currentSelections.semDestination) {
                        console.log(`Skipping color update for currently selected destination: ${positionName}`);
                        return;
                    }
                    
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
        console.log('Updating TEM tray colors with currentSelections:', this.currentSelections);
        Object.keys(positions).forEach(positionName => {
            const positionData = positions[positionName];
            const rect = document.getElementById(positionName);
            
            if (rect) {
                // Skip only if this is the CURRENT selection (after we update currentSelections)
                if (positionName === this.currentSelections.temOrigin || 
                    positionName === this.currentSelections.temDestination) {
                    console.log(`Skipping color update for currently selected position: ${positionName}`);
                    return;
                }
                
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
        console.log('Updating dropdown options for', systemType, pageType);
        
        if (pageType === 'tray') {
            if (systemType === 'sem') {
                // SEM Tray page - filter origin dropdown
                const originSelect = document.getElementById('origin');
                if (originSelect) {
                    this.filterDropdownOptions(originSelect, positions, 'tray', 'clean');
                }
            } else if (systemType === 'tem') {
                // TEM Tray page - filter both dropdowns  
                const originSelect = document.getElementById('origin');
                const destinationSelect = document.getElementById('destination');
                
                if (originSelect) {
                    // Origin: only show clean TC positions
                    this.filterDropdownOptions(originSelect, positions, 'origin', 'clean');
                }
                
                if (destinationSelect) {
                    // Destination: only show empty TE positions
                    this.filterDropdownOptions(destinationSelect, positions, 'destination', 'empty');
                }
            }
        } else if (pageType === 'stage' && systemType === 'sem') {
            // SEM Stage page - filter both dropdowns
            const originSelect = document.getElementById('origin');
            const destinationSelect = document.getElementById('destination');
            
            if (originSelect) {
                // Origin: only show clean tray positions
                this.filterDropdownOptions(originSelect, positions, 'tray', 'clean');
            }
            
            if (destinationSelect) {
                // Destination: only show empty stage positions
                this.filterDropdownOptions(destinationSelect, positions, 'stage', 'empty');
            }
        }
    },

    // New helper function to filter dropdown options
    filterDropdownOptions(selectElement, positions, positionType, requiredStatus) {
        Array.from(selectElement.options).forEach(option => {
            if (!option.value) {
                // Keep the default "Select..." option
                return;
            }
            
            const positionData = positions[option.value];
            let shouldShow = false;
            
            if (positionData) {
                // Check if position matches our filtering criteria
                if (positionType === 'origin') {
                    // TEM origin: show TC positions that are clean
                    shouldShow = option.value.startsWith('TC') && positionData.status === requiredStatus;
                } else if (positionType === 'destination') {
                    // TEM destination: show TE positions that are empty
                    shouldShow = option.value.startsWith('TE') && positionData.status === requiredStatus;
                } else if (positionType === 'tray') {
                    // SEM tray positions: show clean positions
                    shouldShow = positionData.type === 'tray' && positionData.status === requiredStatus;
                } else if (positionType === 'stage') {
                    // SEM stage positions: show empty positions
                    shouldShow = positionData.type === 'stage' && positionData.status === requiredStatus;
                }
            } else {
                // Position not in database - check for permanently forbidden stage positions
                if (positionType === 'stage') {
                    const stageId = option.value.startsWith('PH_STUB_') 
                        ? `stg_${option.value.replace('PH_STUB_', '').toLowerCase()}`
                        : option.value;
                    const stageElement = document.getElementById(stageId);
                    
                    if (stageElement && stageElement.classList.contains('forbidden-position')) {
                        shouldShow = false; // Hide permanently forbidden positions
                    } else {
                        shouldShow = false; // Hide unknown positions by default
                    }
                } else {
                    shouldShow = false; // Hide unknown positions
                }
            }
            
            // Show/hide the option
            if (shouldShow) {
                option.style.display = '';
                option.disabled = false;
                option.style.color = 'black';
            } else {
                option.style.display = 'none'; // Actually hide instead of just disabling
                option.disabled = true;
            }
        });
        
        console.log(`Filtered ${selectElement.id} dropdown for ${positionType} positions with status ${requiredStatus}`);
    }
};

// Page initialization function that gets called after content loads
window.initializePositionSystem = async function() {
    console.log('Initializing position system...');
    
    // IMPORTANT: Reset all current selections on page load
    window.positionManager.currentSelections = {
        semOrigin: null,
        semDestination: null,
        temOrigin: null,
        temDestination: null
    };
    console.log('Reset all current selections to null');
    
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
            window.positionManager.updateDropdownOptions('tem', 'tray');
            
        } else {
            console.log('No position system detected on this page');
        }
    }, 500);
    
    // Add event listeners to dropdowns - SIMPLIFIED VERSION ONLY
    setTimeout(() => {
        const originSelect = document.getElementById('origin');
        const destinationSelect = document.getElementById('destination');
        
        if (originSelect) {
            // Remove any existing event listeners first
            originSelect.removeEventListener('change', originSelect._changeHandler);
            
            // Add new clean event listener
            const originChangeHandler = function() {
                console.log('Origin dropdown changed to:', this.value);
                
                // Just call the appropriate enhanced function - don't duplicate logic
                if (document.getElementById('stageContainer')) {
                    // SEM Stage page
                    window.enhancedSEMOriginChange(this);
                } else if (document.querySelector('rect[id^="TC"]')) {
                    // TEM Tray page
                    window.enhancedTEMOriginChange(this);
                } else if (document.querySelector('circle[id^="A"]')) {
                    // SEM Tray page
                    window.enhancedSEMOriginChange(this);
                }
            };
            
            originSelect.addEventListener('change', originChangeHandler);
            originSelect._changeHandler = originChangeHandler; // Store reference for cleanup
        }
        
        if (destinationSelect) {
            // Remove any existing event listeners first
            destinationSelect.removeEventListener('change', destinationSelect._changeHandler);
            
            // Add new clean event listener
            const destinationChangeHandler = function() {
                console.log('Destination dropdown changed to:', this.value);
                
                // Just call the appropriate enhanced function - don't duplicate logic
                if (document.getElementById('stageContainer')) {
                    // SEM Stage page
                    window.enhancedSEMDestinationChange(this);
                } else if (document.querySelector('rect[id^="TC"]')) {
                    // TEM Tray page
                    window.enhancedTEMDestinationChange(this);
                }
            };
            
            destinationSelect.addEventListener('change', destinationChangeHandler);
            destinationSelect._changeHandler = destinationChangeHandler; // Store reference for cleanup
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

// Enhanced dropdown change handlers - EXPLICIT RESTORATION APPROACH
window.enhancedSEMOriginChange = function(selectElement) {
    console.log('enhancedSEMOriginChange called with:', selectElement.value);
    const selectedValue = selectElement.value;
    const previousSelection = window.positionManager.currentSelections.semOrigin;
    
    // STEP 1: Explicitly restore the previous selection's color if it exists
    if (previousSelection && window.positionManager.positions.sem && window.positionManager.positions.sem[previousSelection]) {
        const prevElement = document.getElementById(previousSelection);
        if (prevElement) {
            const prevPositionData = window.positionManager.positions.sem[previousSelection];
            const originalColor = window.positionManager.colorScheme[prevPositionData.status] || window.positionManager.colorScheme.unavailable;
            
            prevElement.setAttribute('fill', originalColor);
            prevElement.style.fill = originalColor;
            console.log(`Restored previous selection ${previousSelection} to ${prevPositionData.status} color (${originalColor})`);
        }
    }
    
    // STEP 2: Update current selection tracking
    window.positionManager.currentSelections.semOrigin = selectedValue;
    
    // STEP 3: Highlight the new selected position (if any)
    if (selectedValue) {
        const selectedCircle = document.getElementById(selectedValue);
        if (selectedCircle) {
            selectedCircle.setAttribute('fill', window.positionManager.colorScheme.selected);
            selectedCircle.style.fill = window.positionManager.colorScheme.selected;
            console.log('Highlighted', selectedValue, 'with gold color');
        }
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
    const previousSelection = window.positionManager.currentSelections.semDestination;
    
    // STEP 1: Explicitly restore the previous selection's color if it exists
    if (previousSelection && window.positionManager.positions.sem && window.positionManager.positions.sem[previousSelection]) {
        // Convert to stage ID format
        let prevStageId;
        if (previousSelection.startsWith('PH_STUB_')) {
            const stageName = previousSelection.replace('PH_STUB_', '').toLowerCase();
            prevStageId = `stg_${stageName}`;
        } else {
            prevStageId = previousSelection;
        }
        
        const prevElement = document.getElementById(prevStageId);
        if (prevElement && !prevElement.classList.contains('forbidden-position')) {
            const prevPositionData = window.positionManager.positions.sem[previousSelection];
            const originalColor = window.positionManager.colorScheme[prevPositionData.status] || window.positionManager.colorScheme.unavailable;
            
            prevElement.setAttribute('fill', originalColor);
            prevElement.style.fill = originalColor;
            console.log(`Restored previous selection ${previousSelection} (${prevStageId}) to ${prevPositionData.status} color (${originalColor})`);
        }
    }
    
    // STEP 2: Update current selection tracking
    window.positionManager.currentSelections.semDestination = selectedValue;
    
    // STEP 3: Highlight the new selected stage position
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
                // Reset the selection back to previous
                window.positionManager.currentSelections.semDestination = previousSelection;
                selectElement.value = previousSelection || '';
                return;
            }
        }
    }
};

window.enhancedTEMOriginChange = function(selectElement) {
    console.log('enhancedTEMOriginChange called with:', selectElement.value);
    const selectedValue = selectElement.value;
    const previousSelection = window.positionManager.currentSelections.temOrigin;
    
    // STEP 1: Explicitly restore the previous selection's color if it exists
    if (previousSelection && window.positionManager.positions.tem && window.positionManager.positions.tem[previousSelection]) {
        const prevElement = document.getElementById(previousSelection);
        if (prevElement) {
            const prevPositionData = window.positionManager.positions.tem[previousSelection];
            const originalColor = window.positionManager.colorScheme[prevPositionData.status] || window.positionManager.colorScheme.unavailable;
            
            prevElement.setAttribute('fill', originalColor);
            prevElement.style.fill = originalColor;
            console.log(`Restored previous TEM origin selection ${previousSelection} to ${prevPositionData.status} color (${originalColor})`);
        }
    }
    
    // STEP 2: Update current selection tracking
    window.positionManager.currentSelections.temOrigin = selectedValue;
    
    // STEP 3: Highlight selected origin with GOLD (for clean TC positions)
    if (selectedValue) {
        const selectedRect = document.getElementById(selectedValue);
        if (selectedRect) {
            selectedRect.setAttribute('fill', window.positionManager.colorScheme.selected);
            selectedRect.style.fill = window.positionManager.colorScheme.selected;
            console.log('Highlighted TEM origin', selectedValue, 'with gold color');
        }
    }
};

window.enhancedTEMDestinationChange = function(selectElement) {
    console.log('enhancedTEMDestinationChange called with:', selectElement.value);
    const selectedValue = selectElement.value;
    const previousSelection = window.positionManager.currentSelections.temDestination;
    
    // STEP 1: Explicitly restore the previous selection's color if it exists
    if (previousSelection && window.positionManager.positions.tem && window.positionManager.positions.tem[previousSelection]) {
        const prevElement = document.getElementById(previousSelection);
        if (prevElement) {
            const prevPositionData = window.positionManager.positions.tem[previousSelection];
            const originalColor = window.positionManager.colorScheme[prevPositionData.status] || window.positionManager.colorScheme.unavailable;
            
            prevElement.setAttribute('fill', originalColor);
            prevElement.style.fill = originalColor;
            console.log(`Restored previous TEM destination selection ${previousSelection} to ${prevPositionData.status} color (${originalColor})`);
        }
    }
    
    // STEP 2: Update current selection tracking  
    window.positionManager.currentSelections.temDestination = selectedValue;
    
    // STEP 3: Highlight selected destination with LIGHT BLUE (for TE positions)
    if (selectedValue) {
        const selectedRect = document.getElementById(selectedValue);
        if (selectedRect) {
            selectedRect.setAttribute('fill', window.positionManager.colorScheme.selectedDestination);
            selectedRect.style.fill = window.positionManager.colorScheme.selectedDestination;
            console.log('Highlighted TEM destination', selectedValue, 'with light blue color');
        }
    }
};