// static/js/history.js

class ExperimentHistory {
    constructor() {
        this.init();
        this.checkForRepeatExperiment();
    }

    init() {
        document.addEventListener('DOMContentLoaded', () => {
            console.log('History page DOM loaded');
            if (document.getElementById('historyContainer')) {
                console.log('History container found, binding events');
                this.bindEventListeners();
                this.loadHistory();
            } else {
                console.log('History container not found');
            }
            this.checkForRepeatExperiment();
        });
    }

    checkForRepeatExperiment() {
        // Check if we're on a procedural page and have stored parameters
        const storedParams = localStorage.getItem('repeatExperimentParams');
        if (storedParams) {
            try {
                const params = JSON.parse(storedParams);
                
                // Check if we're on the right page for these parameters
                const currentHash = window.location.hash.replace('#', '');
                let shouldPopulate = false;
                
                // Determine if current page matches the experiment type
                if (currentHash === 'sem-tray' || currentHash === 'sem-stage' || currentHash === 'tem-tray') {
                    shouldPopulate = true;
                }
                
                if (shouldPopulate) {
                    // Wait a bit for the page to fully load, then populate
                    setTimeout(() => {
                        this.populateFormFields(params);
                        localStorage.removeItem('repeatExperimentParams');
                        
                        // Show a notification
                        this.showNotification('Form populated with previous experiment parameters', 'success');
                    }, 500);
                }
            } catch (error) {
                console.error('Error parsing stored experiment parameters:', error);
                localStorage.removeItem('repeatExperimentParams'); // Clear bad data
            }
        }
    }

    populateFormFields(params) {
        console.log('Populating form with parameters:', params);
        
        Object.entries(params).forEach(([key, value]) => {
            const element = document.getElementById(key);
            if (element) {
                if (element.type === 'checkbox') {
                    element.checked = value === 'true' || value === true;
                } else if (element.tagName === 'SELECT') {
                    element.value = value;
                } else {
                    element.value = value;
                }
                
                // Trigger change events in case there are listeners
                element.dispatchEvent(new Event('change'));
            }
        });
    }

    showNotification(message, type = 'info') {
        // Create a notification element
        const notification = document.createElement('div');
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 15px 20px;
            border-radius: 5px;
            color: white;
            font-weight: bold;
            z-index: 10000;
            max-width: 300px;
            background-color: ${type === 'success' ? '#4CAF50' : type === 'error' ? '#f44336' : '#2196F3'};
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        `;
        notification.textContent = message;
        
        document.body.appendChild(notification);
        
        // Remove after 4 seconds
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 4000);
    }

    bindEventListeners() {
        const successCheckbox = document.getElementById('successfulOnly');
        const exportCsvBtn = document.getElementById('exportCsvBtn');
        const exportJsonBtn = document.getElementById('exportJsonBtn');
        const refreshBtn = document.getElementById('refreshBtn');

        console.log('Binding event listeners...');
        console.log('Elements found:', {
            successCheckbox: !!successCheckbox,
            exportCsvBtn: !!exportCsvBtn,
            exportJsonBtn: !!exportJsonBtn,
            refreshBtn: !!refreshBtn
        });

        if (successCheckbox) {
            successCheckbox.addEventListener('change', () => {
                console.log('Checkbox changed, reloading history');
                this.loadHistory();
            });
        }

        if (exportCsvBtn) {
            exportCsvBtn.addEventListener('click', () => {
                console.log('Export CSV clicked');
                this.exportData('csv');
            });
        }

        if (exportJsonBtn) {
            exportJsonBtn.addEventListener('click', () => {
                console.log('Export JSON clicked');
                this.exportData('json');
            });
        }

        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                console.log('Refresh clicked');
                this.refreshHistory();
            });
        }
    }

    loadHistory() {
        console.log('Loading history...');
        const successfulOnly = document.getElementById('successfulOnly')?.checked ?? true;
        const url = `/api/experiment_history?success_only=${successfulOnly}`;
        
        console.log('Fetching from URL:', url);
        
        const container = document.getElementById('historyContainer');
        if (container) {
            container.innerHTML = '<p>Loading experiment history...</p>';
        }

        fetch(url)
            .then(response => {
                console.log('Response status:', response.status);
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                console.log('Received data:', data);
                this.displayHistory(data);
            })
            .catch(error => {
                console.error('Error loading history:', error);
                if (container) {
                    container.innerHTML = `<p style="color: red;">Error loading history: ${error.message}</p>`;
                }
            });
    }

    displayHistory(experiments) {
        console.log('Displaying history with', experiments.length, 'experiments');
        const container = document.getElementById('historyContainer');
        if (!container) {
            console.error('History container not found');
            return;
        }

        if (experiments.length === 0) {
            container.innerHTML = '<p>No experiments found. Try running some experiments first, or uncheck "Show successful experiments only" to see failed experiments.</p>';
            return;
        }

        let html = `
            <div style="overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; min-width: 800px;">
                    <thead>
                        <tr style="background-color: #f4f4f4;">
                            <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Date/Time</th>
                            <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Type</th>
                            <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Status</th>
                            <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Project</th>
                            <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Composition</th>
                            <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Duration</th>
                            <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        experiments.forEach(exp => {
            const timestamp = new Date(exp.timestamp).toLocaleString();
            const status = exp.success ? 'Success' : `Failed (${exp.error_category || 'Unknown'})`;
            const duration = exp.duration_seconds ? `${exp.duration_seconds.toFixed(1)}s` : 'N/A';
            const project = exp.parameters?.project_name || 'N/A';
            const composition = exp.parameters?.composition || 'N/A';

            html += `
                <tr style="border-bottom: 1px solid #eee;">
                    <td style="border: 1px solid #ddd; padding: 12px;">${timestamp}</td>
                    <td style="border: 1px solid #ddd; padding: 12px;">${exp.process_type}</td>
                    <td style="border: 1px solid #ddd; padding: 12px; color: ${exp.success ? 'green' : 'red'}; font-weight: bold;">${status}</td>
                    <td style="border: 1px solid #ddd; padding: 12px;">${project}</td>
                    <td style="border: 1px solid #ddd; padding: 12px;">${composition}</td>
                    <td style="border: 1px solid #ddd; padding: 12px;">${duration}</td>
                    <td style="border: 1px solid #ddd; padding: 12px;">
                        <button class="btn btn-small" onclick="experimentHistory.showDetails(${exp.id})" style="margin-right: 5px;">Details</button>
                        ${exp.success ? `<button class="btn btn-small" onclick="experimentHistory.repeatExperiment(${exp.id})">Repeat</button>` : ''}
                    </td>
                </tr>
            `;
        });

        html += `
                    </tbody>
                </table>
            </div>
        `;

        container.innerHTML = html;
    }

    exportData(format) {
        console.log('Exporting data as:', format);
        const successfulOnly = document.getElementById('successfulOnly')?.checked ?? true;
        const url = `/api/export_experiments?format=${format}&success_only=${successfulOnly}`;
        
        console.log('Export URL:', url);
        window.open(url, '_blank');
    }

    refreshHistory() {
        console.log('Refreshing history');
        this.loadHistory();
    }

    showDetails(experimentId) {
        console.log('Showing details for experiment:', experimentId);
        // Fetch detailed information for this experiment
        fetch(`/api/experiment_details/${experimentId}`)
            .then(response => response.json())
            .then(data => {
                this.displayDetailsModal(data);
            })
            .catch(error => {
                console.error('Error fetching experiment details:', error);
                alert('Error loading experiment details. Please try again.');
            });
    }

    displayDetailsModal(experiment) {
        console.log('Displaying details modal for experiment:', experiment);
        // Create a modal to show detailed information
        const modal = document.createElement('div');
        modal.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        `;

        const modalContent = document.createElement('div');
        modalContent.style.cssText = `
            background: white;
            padding: 20px;
            border-radius: 8px;
            max-width: 600px;
            max-height: 80vh;
            overflow-y: auto;
            position: relative;
        `;

        let parametersHtml = '';
        if (experiment.parameters) {
            const params = typeof experiment.parameters === 'string' 
                ? JSON.parse(experiment.parameters) 
                : experiment.parameters;
            
            parametersHtml = Object.entries(params)
                .map(([key, value]) => `<p><strong>${key}:</strong> ${value}</p>`)
                .join('');
        }

        modalContent.innerHTML = `
            <h2>Experiment Details</h2>
            <button onclick="this.closest('.modal').remove()" style="position: absolute; top: 10px; right: 15px; background: none; border: none; font-size: 20px; cursor: pointer;">&times;</button>
            <p><strong>ID:</strong> ${experiment.id}</p>
            <p><strong>Timestamp:</strong> ${new Date(experiment.timestamp).toLocaleString()}</p>
            <p><strong>Process Type:</strong> ${experiment.process_type}</p>
            <p><strong>Success:</strong> ${experiment.success ? 'Yes' : 'No'}</p>
            ${experiment.error_category ? `<p><strong>Error Category:</strong> ${experiment.error_category}</p>` : ''}
            ${experiment.duration_seconds ? `<p><strong>Duration:</strong> ${experiment.duration_seconds.toFixed(2)} seconds</p>` : ''}
            <h3>Parameters:</h3>
            <div style="background: #f5f5f5; padding: 10px; border-radius: 4px;">
                ${parametersHtml || '<p>No parameters recorded</p>'}
            </div>
        `;

        modal.className = 'modal';
        modal.appendChild(modalContent);
        document.body.appendChild(modal);

        // Close modal when clicking outside
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        });
    }

    repeatExperiment(experimentId) {
        console.log('Repeating experiment:', experimentId);
        // Fetch experiment data and populate forms
        fetch(`/api/experiment_details/${experimentId}`)
            .then(response => response.json())
            .then(experiment => {
                if (experiment.parameters) {
                    const params = typeof experiment.parameters === 'string' 
                        ? JSON.parse(experiment.parameters) 
                        : experiment.parameters;
                    
                    // Determine which page to navigate to based on process type
                    let targetPage = '';
                    if (experiment.process_type === 'sem_process') {
                        // Check if it's tray or stage based on destination
                        targetPage = params.destination === 'tray' ? 'sem-tray' : 'sem-stage';
                    } else if (experiment.process_type === 'tem_process') {
                        targetPage = 'tem-tray';
                    } else {
                        alert('Cannot repeat this type of experiment automatically.');
                        return;
                    }

                    // Store parameters in localStorage for the next page
                    localStorage.setItem('repeatExperimentParams', JSON.stringify(params));
                    
                    // Navigate to the appropriate page
                    window.location.hash = targetPage;
                    
                    // Show a message
                    this.showNotification('Navigating to experiment page...', 'info');
                } else {
                    alert('No parameters found for this experiment.');
                }
            })
            .catch(error => {
                console.error('Error fetching experiment for repeat:', error);
                alert('Error loading experiment data. Please try again.');
            });
    }
}

// Create global instance
const experimentHistory = new ExperimentHistory();

// Also listen for hash changes (page navigation) to check for repeat experiments
window.addEventListener('hashchange', () => {
    setTimeout(() => {
        experimentHistory.checkForRepeatExperiment();
    }, 500);
});