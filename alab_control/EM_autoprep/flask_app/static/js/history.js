// static/js/history.js

class ExperimentHistory {
    constructor() {
        // Don't initialize immediately, let the page load first
        this.initialized = false;
    }

    init() {
        if (this.initialized) return;
        this.initialized = true;
        
        console.log('Initializing ExperimentHistory');
        this.bindEventListeners();
        this.loadHistory();
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
                this.loadHistory();
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
                        <button class="btn btn-small" onclick="window.experimentHistory.showDetails(${exp.id})">Details</button>
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

    showDetails(experimentId) {
        alert('Details functionality not implemented yet for experiment ' + experimentId);
    }
}

// Create global instance
window.experimentHistory = new ExperimentHistory();

// Listen for when the history page is loaded
document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('historyContainer')) {
        window.experimentHistory.init();
    }
});