import sqlite3
import json
import os
from datetime import datetime, timedelta
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
    

def get_recent_operations_with_details(limit=10):
    """
    Get recent operations with detailed failure information.
    
    Args:
        limit (int): Maximum number of operations to return
    
    Returns:
        list: List of dictionaries with operation details and failure points
    """
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
                    GROUP_CONCAT(ed.error_message, ' | ') as error_messages,
                    GROUP_CONCAT(ed.component, ', ') as failed_components,
                    COUNT(ed.id) as error_count
                FROM process_runs pr
                LEFT JOIN error_details ed ON pr.id = ed.process_run_id
                GROUP BY pr.id
                ORDER BY pr.timestamp DESC
                LIMIT ?
            """, (limit,))
            
            operations = []
            for row in cursor.fetchall():
                operation = dict(row)
                
                # Parse parameters if available
                if operation['parameters']:
                    try:
                        operation['parsed_parameters'] = json.loads(operation['parameters'])
                    except:
                        operation['parsed_parameters'] = {}
                else:
                    operation['parsed_parameters'] = {}
                
                # Format timestamp for display
                if operation['timestamp']:
                    dt = datetime.fromisoformat(operation['timestamp'])
                    operation['formatted_time'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                    operation['time_ago'] = get_time_ago(dt)
                
                # Determine failure stage based on error messages
                if not operation['success'] and operation['error_messages']:
                    operation['failure_stage'] = determine_failure_stage(operation['error_messages'])
                else:
                    operation['failure_stage'] = 'Completed Successfully'
                
                operations.append(operation)
            
            return operations
            
    except Exception as e:
        print(f"Error getting recent operations: {e}")
        return []

def determine_failure_stage(error_messages):
    """Determine which stage of the process failed based on error messages."""
    if not error_messages:
        return "Unknown"
    
    error_text = error_messages.lower()
    
    # Define failure stages based on error patterns
    if any(keyword in error_text for keyword in ['home', 'homing', 'gohome']):
        return "Robot Homing"
    elif any(keyword in error_text for keyword in ['intermediate position', 'moving to']):
        return "Robot Positioning"
    elif any(keyword in error_text for keyword in ['vacuum', 'enabling vacuum']):
        return "Vacuum System"
    elif any(keyword in error_text for keyword in ['stub not picked', 'grid not picked', 'laser', 'detection']):
        return "Sample Pickup"
    elif any(keyword in error_text for keyword in ['charging', 'exposure', 'hvps']):
        return "Sample Exposure"
    elif any(keyword in error_text for keyword in ['delivery', 'delivering', 'stage']):
        return "Sample Delivery"
    elif any(keyword in error_text for keyword in ['final cleanup', 'device_step_final']):
        return "Final Cleanup"
    elif any(keyword in error_text for keyword in ['control panel', 'plc', 'timeout', 'no response']):
        return "Control Panel Communication"
    elif any(keyword in error_text for keyword in ['robot', 'connection', 'serial']):
        return "Robot Communication"
    elif any(keyword in error_text for keyword in ['vibration', 'motor']):
        return "Vibration Motors"
    else:
        return "Process Execution"

def get_time_ago(dt):
    """Get human-readable time difference."""
    now = datetime.now()
    diff = now - dt
    
    if diff.days > 0:
        return f"{diff.days} day{'s' if diff.days != 1 else ''} ago"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    else:
        return "Just now"
    
#region Soak test functions

def init_soak_test_database():
    """
    Initialize soak test database tables.
    Call this function after your existing init_database() function.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Create soak_test_sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS soak_test_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_type TEXT NOT NULL,
                start_time DATETIME NOT NULL,
                end_time DATETIME,
                status TEXT NOT NULL DEFAULT 'running',
                target_cycles INTEGER NOT NULL,
                completed_cycles INTEGER DEFAULT 0,
                max_retries INTEGER NOT NULL,
                failure_handling TEXT NOT NULL,
                recovery_mode TEXT NOT NULL,
                current_cycle INTEGER DEFAULT 0,
                current_position TEXT,
                current_step TEXT,
                total_operations INTEGER DEFAULT 0,
                successful_operations INTEGER DEFAULT 0,
                failed_operations INTEGER DEFAULT 0,
                current_success_rate REAL DEFAULT 0.0,
                final_success_rate REAL,
                failure_reason TEXT,
                test_parameters TEXT,
                duration_minutes REAL,
                estimated_completion DATETIME
            )
        """)
        
        # Create soak_test_cycles table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS soak_test_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                cycle_number INTEGER NOT NULL,
                start_time DATETIME NOT NULL,
                end_time DATETIME,
                status TEXT NOT NULL DEFAULT 'running',
                total_positions INTEGER DEFAULT 0,
                successful_positions INTEGER DEFAULT 0,
                failed_positions INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.0,
                cycle_data TEXT,
                FOREIGN KEY (session_id) REFERENCES soak_test_sessions (id)
            )
        """)
        
        # Create soak_test_operations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS soak_test_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                cycle_id INTEGER,
                operation_type TEXT NOT NULL,
                position TEXT,
                start_time DATETIME NOT NULL,
                end_time DATETIME,
                success BOOLEAN NOT NULL DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                operation_data TEXT,
                error_message TEXT,
                duration_seconds REAL,
                FOREIGN KEY (session_id) REFERENCES soak_test_sessions (id),
                FOREIGN KEY (cycle_id) REFERENCES soak_test_cycles (id)
            )
        """)
        
        # Create soak_test_errors table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS soak_test_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                operation_id INTEGER,
                timestamp DATETIME NOT NULL,
                error_category TEXT NOT NULL,
                error_message TEXT NOT NULL,
                position TEXT,
                cycle_number INTEGER,
                component TEXT,
                recovery_action TEXT,
                FOREIGN KEY (session_id) REFERENCES soak_test_sessions (id),
                FOREIGN KEY (operation_id) REFERENCES soak_test_operations (id)
            )
        """)
        
        # Create indexes for better performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_soak_sessions_type_time 
            ON soak_test_sessions(test_type, start_time)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_soak_cycles_session 
            ON soak_test_cycles(session_id, cycle_number)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_soak_operations_session 
            ON soak_test_operations(session_id, start_time)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_soak_errors_session 
            ON soak_test_errors(session_id, timestamp)
        """)
        
        conn.commit()
        print("Soak test database tables initialized successfully")

def create_soak_test_session(test_type, config):
    """
    Create a new soak test session.
    
    Args:
        test_type (str): Type of test ('sem_pick_place', 'tem_cycling', etc.)
        config (dict): Test configuration parameters
    
    Returns:
        int: Session ID of the created session
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO soak_test_sessions (
                    test_type, start_time, target_cycles, max_retries,
                    failure_handling, recovery_mode, test_parameters
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                test_type,
                datetime.now(),
                config.get('cycles', 1),
                config.get('max_retries', 3),
                config.get('failure_handling', 'skip'),
                config.get('recovery_mode', 'continue'),
                json.dumps(config)
            ))
            
            session_id = cursor.lastrowid
            conn.commit()
            
            print(f"Created soak test session {session_id}: {test_type}")
            return session_id
            
    except Exception as e:
        print(f"Error creating soak test session: {e}")
        return None

def update_soak_test_session(session_id, updates):
    """
    Update a soak test session with new data.
    
    Args:
        session_id (int): Session ID to update
        updates (dict): Dictionary of fields to update
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Build dynamic update query
            update_fields = []
            values = []
            
            for field, value in updates.items():
                update_fields.append(f"{field} = ?")
                values.append(value)
            
            if update_fields:
                values.append(session_id)
                query = f"""
                    UPDATE soak_test_sessions 
                    SET {', '.join(update_fields)}
                    WHERE id = ?
                """
                cursor.execute(query, values)
                conn.commit()
                
    except Exception as e:
        print(f"Error updating soak test session {session_id}: {e}")

def get_soak_test_session(session_id):
    """Get soak test session data."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM soak_test_sessions WHERE id = ?
            """, (session_id,))
            
            row = cursor.fetchone()
            return dict(row) if row else None
            
    except Exception as e:
        print(f"Error getting soak test session {session_id}: {e}")
        return None

def get_current_soak_test_session():
    """Get the currently running soak test session."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM soak_test_sessions 
                WHERE status IN ('running', 'paused')
                ORDER BY start_time DESC
                LIMIT 1
            """)
            
            row = cursor.fetchone()
            return dict(row) if row else None
            
    except Exception as e:
        print(f"Error getting current soak test session: {e}")
        return None

def create_soak_test_cycle(session_id, cycle_number):
    """Create a new test cycle within a session."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO soak_test_cycles (
                    session_id, cycle_number, start_time
                ) VALUES (?, ?, ?)
            """, (session_id, cycle_number, datetime.now()))
            
            cycle_id = cursor.lastrowid
            conn.commit()
            
            return cycle_id
            
    except Exception as e:
        print(f"Error creating soak test cycle: {e}")
        return None

def log_soak_test_operation(session_id, operation_type, position, success, 
                           cycle_id=None, retry_count=0, error_message=None, 
                           duration_seconds=None, operation_data=None):
    """Log a single soak test operation."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO soak_test_operations (
                    session_id, cycle_id, operation_type, position, start_time,
                    end_time, success, retry_count, operation_data, error_message,
                    duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, cycle_id, operation_type, position,
                datetime.now() - timedelta(seconds=duration_seconds or 0),
                datetime.now(), success, retry_count,
                json.dumps(operation_data) if operation_data else None,
                error_message, duration_seconds
            ))
            
            operation_id = cursor.lastrowid
            conn.commit()
            
            return operation_id
            
    except Exception as e:
        print(f"Error logging soak test operation: {e}")
        return None

def log_soak_test_error(session_id, error_category, error_message, 
                       position=None, cycle_number=None, component=None, 
                       operation_id=None, recovery_action=None):
    """Log a soak test error."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO soak_test_errors (
                    session_id, operation_id, timestamp, error_category,
                    error_message, position, cycle_number, component, recovery_action
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, operation_id, datetime.now(), error_category,
                error_message, position, cycle_number, component, recovery_action
            ))
            
            conn.commit()
            
    except Exception as e:
        print(f"Error logging soak test error: {e}")

def get_soak_test_statistics():
    """Get overall soak test statistics for the dashboard."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Get success rates by test type
            cursor.execute("""
                SELECT 
                    test_type,
                    AVG(final_success_rate) as avg_success_rate,
                    COUNT(*) as total_tests
                FROM soak_test_sessions 
                WHERE status = 'completed' AND final_success_rate IS NOT NULL
                GROUP BY test_type
            """)
            
            stats = {}
            for row in cursor.fetchall():
                row_dict = dict(row)
                test_type = row_dict['test_type']
                stats[f"{test_type}_rate"] = round(row_dict['avg_success_rate'], 1)
            
            return stats
            
    except Exception as e:
        print(f"Error getting soak test statistics: {e}")
        return {}

def get_recent_soak_tests(limit=10):
    """Get recent soak test sessions for the dashboard."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    id, test_type, start_time, end_time, status,
                    target_cycles, completed_cycles, final_success_rate,
                    duration_minutes
                FROM soak_test_sessions 
                ORDER BY start_time DESC 
                LIMIT ?
            """, (limit,))
            
            tests = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                # Add computed fields
                row_dict['cycles_completed'] = row_dict['completed_cycles'] or 0
                row_dict['target_cycles'] = row_dict['target_cycles'] or 0
                row_dict['success_rate'] = row_dict['final_success_rate']
                tests.append(row_dict)
            
            return tests
            
    except Exception as e:
        print(f"Error getting recent soak tests: {e}")
        return []

def get_soak_test_errors_for_session(session_id, limit=20):
    """Get recent errors for a specific test session."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT timestamp, error_message, position, cycle_number, component
                FROM soak_test_errors 
                WHERE session_id = ?
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (session_id, limit))
            
            return [dict(row) for row in cursor.fetchall()]
            
    except Exception as e:
        print(f"Error getting soak test errors for session {session_id}: {e}")
        return []

# Soak test specific error categories
SOAK_TEST_ERROR_CATEGORIES = {
    'Robot_Movement': 'Error during robot movement operations',
    'Robot_Communication': 'Communication failure with 3D printer',
    'PLC_Communication': 'Communication failure with PLC',
    'Laser_Detection': 'Laser sensor detection failure',
    'Vacuum_System': 'Vacuum pump or suction failure',
    'Position_Error': 'Incorrect positioning or calibration',
    'Gripper_Error': 'Gripper operation failure',
    'Rotator_Error': 'Rotator mechanism failure',
    'Timeout_Error': 'Operation timeout',
    'User_Abort': 'User requested stop/abort',
    'System_Error': 'General system or software error',
    'Test_Logic': 'Error in test logic or sequencing'
}

def determine_soak_error_category(error_message, operation_type):
    """Determine error category for soak test errors."""
    error_lower = error_message.lower()
    
    if any(keyword in error_lower for keyword in ['robot', '3dp', 'printer', 'movement', 'position']):
        if 'communication' in error_lower or 'connection' in error_lower:
            return 'Robot_Communication'
        elif 'position' in error_lower or 'coordinate' in error_lower:
            return 'Position_Error'
        else:
            return 'Robot_Movement'
    elif any(keyword in error_lower for keyword in ['plc', 'socket', 'timeout', 'macstat']):
        return 'PLC_Communication'
    elif any(keyword in error_lower for keyword in ['laser', 'detection', 'sensor']):
        return 'Laser_Detection'
    elif any(keyword in error_lower for keyword in ['vacuum', 'suction', 'pump']):
        return 'Vacuum_System'
    elif any(keyword in error_lower for keyword in ['gripper', 'grip']):
        return 'Gripper_Error'
    elif any(keyword in error_lower for keyword in ['rotator', 'rotation', 'flip']):
        return 'Rotator_Error'
    elif any(keyword in error_lower for keyword in ['timeout', 'time out']):
        return 'Timeout_Error'
    elif any(keyword in error_lower for keyword in ['abort', 'stop', 'cancel']):
        return 'User_Abort'
    elif 'test' in error_lower or operation_type in error_lower:
        return 'Test_Logic'
    else:
        return 'System_Error'
    
#endregion