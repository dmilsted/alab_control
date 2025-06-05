import sqlite3
import json
import os
from datetime import datetime
from contextlib import contextmanager
import threading

# Thread-local storage for database connections
_local = threading.local()

# Database file path - same directory as this script
DB_PATH = os.path.join(os.path.dirname(__file__), 'em_autoprep.db')

@contextmanager
def get_db_connection():
    """
    Context manager for database connections.
    Uses thread-local storage to ensure thread safety.
    """
    if not hasattr(_local, 'connection') or _local.connection is None:
        _local.connection = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.connection.row_factory = sqlite3.Row  # Enable dict-like access to rows
    
    try:
        yield _local.connection
    except Exception as e:
        _local.connection.rollback()
        raise e

def init_database():
    """
    Initialize the database with required tables.
    This should be called once when the app starts.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Create process_runs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS process_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                process_type TEXT NOT NULL,
                success BOOLEAN NOT NULL DEFAULT 0,
                error_category TEXT,
                duration_seconds REAL,
                parameters TEXT
            )
        """)
        
        # Create error_details table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS error_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                process_run_id INTEGER,
                timestamp DATETIME NOT NULL,
                error_message TEXT NOT NULL,
                component TEXT,
                FOREIGN KEY (process_run_id) REFERENCES process_runs (id)
            )
        """)
        
        # Create indexes for better query performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_process_runs_timestamp 
            ON process_runs(timestamp)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_process_runs_type 
            ON process_runs(process_type)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_error_details_process_run_id 
            ON error_details(process_run_id)
        """)
        
        conn.commit()
        print(f"Database initialized successfully at {DB_PATH}")

def start_process_run(process_type, parameters=None):
    """
    Log the start of a process run.
    
    Args:
        process_type (str): Type of process (e.g., 'SEM_tray', 'TEM_tray', etc.)
        parameters (dict): Process parameters to be stored as JSON
    
    Returns:
        int: The ID of the created process run
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Convert parameters to JSON string if provided
            params_json = json.dumps(parameters) if parameters else None
            
            cursor.execute("""
                INSERT INTO process_runs (timestamp, process_type, parameters)
                VALUES (?, ?, ?)
            """, (datetime.now(), process_type, params_json))
            
            process_run_id = cursor.lastrowid
            conn.commit()
            
            print(f"Started process run {process_run_id}: {process_type}")
            return process_run_id
            
    except Exception as e:
        print(f"Error starting process run: {e}")
        return None

def end_process_run(process_run_id, success, error_category=None, duration_seconds=None):
    """
    Update a process run with completion status.
    
    Args:
        process_run_id (int): ID of the process run to update
        success (bool): Whether the process completed successfully
        error_category (str): Category of error if unsuccessful
        duration_seconds (float): Duration of the process in seconds
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE process_runs 
                SET success = ?, error_category = ?, duration_seconds = ?
                WHERE id = ?
            """, (success, error_category, duration_seconds, process_run_id))
            
            conn.commit()
            
            status = "SUCCESS" if success else f"FAILED ({error_category})"
            print(f"Ended process run {process_run_id}: {status}")
            
    except Exception as e:
        print(f"Error ending process run {process_run_id}: {e}")

def log_error(process_run_id, error_message, component=None):
    """
    Log an error associated with a process run.
    
    Args:
        process_run_id (int): ID of the associated process run (can be None for standalone errors)
        error_message (str): The error message
        component (str): Component where error occurred ('robot', 'PLC', 'system', etc.)
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO error_details (process_run_id, timestamp, error_message, component)
                VALUES (?, ?, ?, ?)
            """, (process_run_id, datetime.now(), error_message, component))
            
            conn.commit()
            
            print(f"Logged error for process {process_run_id}: {error_message}")
            
    except Exception as e:
        print(f"Error logging error details: {e}")

def log_standalone_error(error_message, component=None):
    """
    Log an error that's not associated with a specific process run.
    
    Args:
        error_message (str): The error message
        component (str): Component where error occurred
    """
    log_error(None, error_message, component)

# Statistics and Query Functions

def get_success_rate_by_process_type(days=30):
    """
    Get success rate for each process type in the last N days.
    
    Args:
        days (int): Number of days to look back
    
    Returns:
        list: List of dictionaries with process_type, total_runs, successful_runs, success_rate
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    process_type,
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_runs,
                    ROUND(
                        (SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)), 2
                    ) as success_rate
                FROM process_runs 
                WHERE timestamp >= datetime('now', '-{} days')
                GROUP BY process_type
                ORDER BY total_runs DESC
            """.format(days))
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting success rates: {e}")
        return []

def get_most_common_errors(limit=10):
    """
    Get the most common error messages.
    
    Args:
        limit (int): Maximum number of errors to return
    
    Returns:
        list: List of dictionaries with error_message, component, count
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    error_message,
                    component,
                    COUNT(*) as count
                FROM error_details 
                GROUP BY error_message, component
                ORDER BY count DESC
                LIMIT ?
            """, (limit,))
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting common errors: {e}")
        return []

def get_process_counts_by_time_period(period='day', days=7):
    """
    Get process counts grouped by time period.
    
    Args:
        period (str): 'day', 'hour', or 'week'
        days (int): Number of days to look back
    
    Returns:
        list: List of dictionaries with time_period, total_processes, successful_processes
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if period == 'day':
                date_format = '%Y-%m-%d'
            elif period == 'hour':
                date_format = '%Y-%m-%d %H:00'
            else:  # week
                date_format = '%Y-W%W'
            
            cursor.execute(f"""
                SELECT 
                    strftime('{date_format}', timestamp) as time_period,
                    COUNT(*) as total_processes,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_processes
                FROM process_runs 
                WHERE timestamp >= datetime('now', '-{days} days')
                GROUP BY strftime('{date_format}', timestamp)
                ORDER BY time_period DESC
            """)
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting process counts: {e}")
        return []

def get_error_categories_summary():
    """
    Get summary of error categories.
    
    Returns:
        list: List of dictionaries with error_category and count
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    error_category,
                    COUNT(*) as count
                FROM process_runs 
                WHERE error_category IS NOT NULL
                GROUP BY error_category
                ORDER BY count DESC
            """)
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting error categories: {e}")
        return []

# Test function to verify database setup
def test_database():
    """
    Test the database functionality.
    """
    print("Testing database functionality...")
    
    # Test process run logging
    test_params = {"voltage": "10000", "time": "5000", "origin": "A1"}
    run_id = start_process_run("TEST_process", test_params)
    
    if run_id:
        # Test error logging
        log_error(run_id, "Test error message", "test_component")
        
        # Test process completion
        end_process_run(run_id, False, "System_Error", 10.5)
        
        print("Database test completed successfully!")
    else:
        print("Database test failed!")

def get_detailed_process_history(days=7):
    """Get detailed process history for the last N days."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    pr.id,
                    pr.timestamp,
                    pr.process_type,
                    pr.success,
                    pr.error_category,
                    pr.duration_seconds,
                    pr.parameters,
                    COUNT(ed.id) as error_count
                FROM process_runs pr
                LEFT JOIN error_details ed ON pr.id = ed.process_run_id
                WHERE pr.timestamp >= datetime('now', '-{} days')
                GROUP BY pr.id
                ORDER BY pr.timestamp DESC
            """.format(days))
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting process history: {e}")
        return []

def get_failure_analysis():
    """Get analysis of failure patterns."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    process_type,
                    error_category,
                    COUNT(*) as failure_count,
                    AVG(duration_seconds) as avg_duration_before_failure,
                    MIN(timestamp) as first_occurrence,
                    MAX(timestamp) as last_occurrence
                FROM process_runs 
                WHERE success = 0 AND error_category IS NOT NULL
                GROUP BY process_type, error_category
                ORDER BY failure_count DESC
            """)
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting failure analysis: {e}")
        return []

def get_performance_metrics():
    """Get performance metrics for successful processes."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    process_type,
                    COUNT(*) as successful_runs,
                    AVG(duration_seconds) as avg_duration,
                    MIN(duration_seconds) as min_duration,
                    MAX(duration_seconds) as max_duration,
                    ROUND(AVG(duration_seconds), 2) as avg_duration_rounded
                FROM process_runs 
                WHERE success = 1 AND duration_seconds IS NOT NULL
                GROUP BY process_type
                ORDER BY successful_runs DESC
            """)
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting performance metrics: {e}")
        return []

def get_error_trends(days=30):
    """Get error trends over time."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    DATE(timestamp) as date,
                    error_category,
                    COUNT(*) as error_count
                FROM process_runs 
                WHERE success = 0 
                    AND error_category IS NOT NULL
                    AND timestamp >= datetime('now', '-{} days')
                GROUP BY DATE(timestamp), error_category
                ORDER BY date DESC, error_count DESC
            """.format(days))
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting error trends: {e}")
        return []

def get_component_reliability():
    """Get reliability statistics by component."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    component,
                    COUNT(*) as total_errors,
                    COUNT(DISTINCT process_run_id) as affected_processes,
                    MIN(timestamp) as first_error,
                    MAX(timestamp) as last_error
                FROM error_details 
                WHERE component IS NOT NULL
                GROUP BY component
                ORDER BY total_errors DESC
            """)
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting component reliability: {e}")
        return []

def search_errors_by_keyword(keyword, days=30):
    """Search for errors containing a specific keyword."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    ed.timestamp,
                    ed.error_message,
                    ed.component,
                    pr.process_type,
                    pr.parameters
                FROM error_details ed
                LEFT JOIN process_runs pr ON ed.process_run_id = pr.id
                WHERE ed.error_message LIKE ?
                    AND ed.timestamp >= datetime('now', '-{} days')
                ORDER BY ed.timestamp DESC
                LIMIT 50
            """.format(days), (f'%{keyword}%',))
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error searching errors: {e}")
        return []

# Example usage functions that you can call for testing
def print_statistics_summary():
    """Print a summary of statistics to console."""
    print("\n" + "="*50)
    print("EM AUTOPREP STATISTICS SUMMARY")
    print("="*50)
    
    # Success rates
    print("\nSUCCESS RATES (Last 30 days):")
    success_rates = get_success_rate_by_process_type(30)
    for rate in success_rates:
        print(f"  {rate['process_type']}: {rate['successful_runs']}/{rate['total_runs']} ({rate['success_rate']}%)")
    
    # Error categories
    print("\nERROR CATEGORIES:")
    error_categories = get_error_categories_summary()
    for category in error_categories:
        print(f"  {category['error_category']}: {category['count']} occurrences")
    
    # Component reliability
    print("\nCOMPONENT RELIABILITY:")
    component_stats = get_component_reliability()
    for comp in component_stats:
        print(f"  {comp['component']}: {comp['total_errors']} errors affecting {comp['affected_processes']} processes")
    
    print("\n" + "="*50)

def export_data_to_csv(filename="em_autoprep_export.csv"):
    """Export process data to CSV file."""
    try:
        import csv
        
        process_history = get_detailed_process_history(30)  # Last 30 days
        
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            if process_history:
                fieldnames = process_history[0].keys()
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(process_history)
                
                print(f"Data exported to {filename}")
                return True
        
        return False
        
    except Exception as e:
        print(f"Error exporting data: {e}")
        return False
    
def get_performance_metrics():
    """Get performance metrics for successful processes."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    process_type,
                    COUNT(*) as successful_runs,
                    AVG(duration_seconds) as avg_duration,
                    MIN(duration_seconds) as min_duration,
                    MAX(duration_seconds) as max_duration
                FROM process_runs 
                WHERE success = 1 AND duration_seconds IS NOT NULL
                GROUP BY process_type
                ORDER BY successful_runs DESC
            """)
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting performance metrics: {e}")
        return []

def get_component_reliability():
    """Get reliability statistics by component."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    component,
                    COUNT(*) as total_errors,
                    COUNT(DISTINCT process_run_id) as affected_processes,
                    MAX(timestamp) as last_error
                FROM error_details 
                WHERE component IS NOT NULL
                GROUP BY component
                ORDER BY total_errors DESC
            """)
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting component reliability: {e}")
        return []