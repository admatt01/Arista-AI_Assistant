import os
import re

def clean_node_name(line):
    """Clean up node names by removing YANG syntax markers."""
    # Remove the YANG tree indicators (+--ro, +--rw, +--) and leading whitespace
    line = re.sub(r'^[\s\+\-|]*(?:ro\s+|rw\s+)?', '', line)
    
    # Extract the main node name (before any special characters or whitespace)
    node_name = line.split()[0] if line.split() else ''
    
    # Remove trailing characters (?, *, etc.)
    node_name = re.sub(r'[?\*]$', '', node_name)
    
    return node_name.strip()

def extract_gnmi_paths(tree_file_path):
    gnmi_paths = []
    current_path = []
    try:
        with open(tree_file_path, 'r') as f:
            lines = f.readlines()

        prev_indent = 0

        for line in lines:
            if not line.strip():
                continue

            # Skip grouping etc.
            if 'grouping' in line or '+---u' in line or 'module:' in line or '->' in line:
                continue

            indent = len(line) - len(line.lstrip())
            node_name = clean_node_name(line)

            if not node_name:
                continue

            key_value_match = re.search(r'$$(.*?)$$', line)
            if key_value_match:
                keys = key_value_match.group(1)
                key_pairs = []
                for pair in keys.split(','):
                    pair = pair.strip()
                    if '=' in pair:
                        key, value = pair.split('=', 1) #Split only at the first =
                        key_pairs.append(f"{key}={value}")
                    elif pair: #Handle keys without values
                        key_pairs.append(pair)


                key_string = ','.join(key_pairs)
                node_name = f"{node_name}[{key_string}]" if key_string else node_name

            # Adjust path based on indentation (simplified logic)
            if indent < prev_indent:
                current_path = current_path[: (prev_indent - indent) // 2] #More efficient way to remove elements
            
            if node_name:
                if indent > prev_indent or not current_path:
                    current_path.append(node_name)
                else:
                    current_path[-1] = node_name #Overwrites the last element correctly

                full_path = '/' + '/'.join(current_path)
                if full_path not in gnmi_paths:
                    gnmi_paths.append(full_path)
            
            prev_indent = indent
    
    except Exception as e:
        print(f"Error processing {tree_file_path}: {e}")
    
    return gnmi_paths

def process_yang_trees(directory):
    """Process all Yang tree files in a given directory."""
    yang_paths = {}
    
    for filename in os.listdir(directory):
        if filename.endswith(('.tree', '.txt')):
            full_path = os.path.join(directory, filename)
            print(f"Processing file: {filename}")
            
            paths = extract_gnmi_paths(full_path)
            
            if paths:
                yang_paths[filename] = sorted(paths)  # Sort paths for better readability
    
    return yang_paths

def save_paths_to_file(gnmi_paths, output_file="gnmi_paths.txt"):
    """Save the extracted paths to a text file."""
    try:
        with open(output_file, 'w') as f:
            for model, paths in gnmi_paths.items():
                f.write(f"\n=== Model: {model} ===\n")
                for path in paths:
                    f.write(f"{path}\n")
        print(f"\nPaths have been saved to {output_file}")
    except Exception as e:
        print(f"Error saving to file: {e}")

def main():
    yang_tree_directory = 'C:/Github_Projects/Arista-gNMI/arista_yang/yang_trees/extraction'
    
    if not os.path.isdir(yang_tree_directory):
        print(f"Error: Directory {yang_tree_directory} does not exist.")
        return
    
    print("Starting to process Yang trees...")
    gnmi_paths = process_yang_trees(yang_tree_directory)
    
    if gnmi_paths:
        save_paths_to_file(gnmi_paths)
        
        print("\nSummary of processed files:")
        for model, paths in gnmi_paths.items():
            print(f"{model}: {len(paths)} paths extracted")
    else:
        print("No paths were extracted from the Yang trees.")

if __name__ == '__main__':
    main()
