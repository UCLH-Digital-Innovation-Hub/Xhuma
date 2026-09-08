import json
import sys

def main():
    if len(sys.argv) != 3:
        print("Usage: python select_targets.py <targets_file> <branch>")
        sys.exit(1)
        
    targets_path = sys.argv[1]
    branch = sys.argv[2]
    
    with open(targets_path, "r") as f:
        inventory = json.load(f)
        
    selected_targets = []
    
    for t in inventory["targets"]:
        if not t.get("enabled", False):
            continue
        if t.get("hold", False):
            continue
            
        expected_branch = t.get("source_branch")
        
        # Temporary mapping for the pilot feature branch
        if branch == "feat/matrix-deployment-pilot" and t.get("stage") == "play":
            selected_targets.append(t)
            continue
            
        if expected_branch == branch:
            selected_targets.append(t)
            
    # Print as a compact JSON array so it can be captured by GitHub Actions
    print(json.dumps(selected_targets))

if __name__ == "__main__":
    main()
