import psutil
import os
import time
import logging
from collections import deque

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log_file = "poe_monitoring.log"
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
logging.getLogger().addHandler(file_handler)

# Display script information
print("==========================================")
print("Enhanced POE CPU Affinity Script - Key Events Only")
print("==========================================")
time.sleep(1)

# Configuration parameters
POSSIBLE_PROCESSES = ["PathOfExileSteam.exe", "PathOfExile.exe", "PathOfExile_x64.exe"]
DISK_USAGE_THRESHOLD_MB = 10  # Threshold for high disk IO activity in MB/s
MEMORY_USAGE_THRESHOLD_MB = 400  # Memory increase threshold in MB
THREAD_COUNT_THRESHOLD = 5  # Number of new threads spawned during loading
CHECK_INTERVAL = 0.25  # Check interval in seconds
HYSTERESIS_THRESHOLD = 20  # Seconds of sustained low activity before restoring affinity
ACTIVITY_WINDOW = 5  # Number of checks for sustained activity tracking

# Function to dynamically create CPU masks for the first half of logical threads
def get_cpu_masks():
    logical_cores = os.cpu_count()
    logging.info(f"Detected {logical_cores} logical cores.")
    
    # Always activate cores 0 and 1, then activate up to half of the remaining cores
    threads_to_activate = logical_cores // 2
    mask = (1 << 0) | (1 << 1)  # Always activate cores 0 and 1

    for i in range(2, threads_to_activate):
        mask |= (1 << i)

    logging.info(f"Generated CPU mask for first half of threads: {bin(mask)}")
    return mask, (1 << logical_cores) - 1  # Full mask for all threads

# Prepare CPU masks
physical_mask, full_mask = get_cpu_masks()

# Function to find target processes
def get_target_process():
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        if proc.info["name"] in POSSIBLE_PROCESSES:
            return proc
    return None

# Function to set CPU affinity for a process
def set_cpu_affinity(process, mask):
    try:
        core_indices = [i for i in range(os.cpu_count()) if (1 << i) & mask]
        process.cpu_affinity(core_indices)
        logging.info(f"Set CPU affinity for {process.info['name']} (PID: {process.pid}) to cores: {core_indices}")
    except Exception as e:
        logging.error(f"Error setting CPU affinity: {e}")

# Function to calculate average over a deque
def calculate_average(data):
    return sum(data) / len(data) if data else 0

# Function to monitor process and detect map transitions
def monitor_process():
    is_smt_disabled = False
    under_threshold_start = None
    previous_io_counters = None
    previous_memory_usage = None
    previous_thread_count = None

    disk_activity_history = deque(maxlen=ACTIVITY_WINDOW)
    memory_activity_history = deque(maxlen=ACTIVITY_WINDOW)

    logging.info("Starting process monitoring...")

    while True:
        process = get_target_process()
        if not process:
            if is_smt_disabled:
                logging.info("Path of Exile process ended. Restoring CPU affinity to all cores.")
                is_smt_disabled = False
            logging.info("Path of Exile not detected. Waiting for start...")
            time.sleep(5)
            continue

        try:
            if previous_io_counters is None:
                logging.info(f"Path of Exile process detected (PID: {process.pid}). Monitoring started.")
                previous_io_counters = process.io_counters()

            # Monitor Disk I/O
            io_counters = process.io_counters()
            read_diff = io_counters.read_bytes - previous_io_counters.read_bytes
            write_diff = io_counters.write_bytes - previous_io_counters.write_bytes
            disk_usage_mb = (read_diff + write_diff) / (1024 * 1024) / CHECK_INTERVAL
            previous_io_counters = io_counters
            disk_activity_history.append(disk_usage_mb)

            # Monitor Memory Usage
            memory_info = process.memory_info()
            memory_usage_mb = memory_info.rss / (1024 * 1024)
            if previous_memory_usage is None:
                previous_memory_usage = memory_usage_mb

            memory_usage_change = memory_usage_mb - previous_memory_usage
            previous_memory_usage = memory_usage_mb
            memory_activity_history.append(memory_usage_change)

            # Monitor Thread Count
            thread_count = process.num_threads()
            if previous_thread_count is None:
                previous_thread_count = thread_count

            thread_count_change = thread_count - previous_thread_count
            previous_thread_count = thread_count

            # Calculate averages for smoothing
            avg_disk_usage = calculate_average(disk_activity_history)
            avg_memory_change = calculate_average(memory_activity_history)

            # Detect High Activity
            if avg_disk_usage >= DISK_USAGE_THRESHOLD_MB or avg_memory_change > MEMORY_USAGE_THRESHOLD_MB or thread_count_change > THREAD_COUNT_THRESHOLD:
                if not is_smt_disabled:
                    logging.info(
                        "High activity detected. Adjusting CPU affinity for map transition. "
                        f"Disk=%.2f MB/s, Memory Change=%.2f MB, Threads Change=%d",
                        avg_disk_usage,
                        avg_memory_change,
                        thread_count_change
                    )
                    set_cpu_affinity(process, physical_mask)
                    is_smt_disabled = True
                    under_threshold_start = None
            else:
                # Low Activity: Wait for sustained inactivity
                if is_smt_disabled:
                    if not under_threshold_start:
                        under_threshold_start = time.time()
                    elif (time.time() - under_threshold_start) >= HYSTERESIS_THRESHOLD:
                        logging.info("Low activity sustained. Restoring CPU affinity to all cores.")
                        set_cpu_affinity(process, full_mask)
                        is_smt_disabled = False

        except psutil.NoSuchProcess:
            logging.info("Path of Exile process terminated.")
            previous_io_counters = None
            previous_memory_usage = None
            previous_thread_count = None
            time.sleep(5)
        except Exception as e:
            logging.error(f"Error monitoring process: {e}")

        time.sleep(CHECK_INTERVAL)

# Main function to start monitoring
def main():
    logging.info("Press CTRL+C to exit.")
    logging.info("Waiting for Path of Exile process...")
    monitor_process()

if __name__ == "__main__":
    main()
