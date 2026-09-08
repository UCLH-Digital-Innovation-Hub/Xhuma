import json
import sys
import jsonschema

def main():
    if len(sys.argv) != 3:
        print("Usage: python validate_targets.py <schema_file> <targets_file>")
        sys.exit(1)
        
    schema_path = sys.argv[1]
    targets_path = sys.argv[2]
    
    with open(schema_path, "r") as f:
        schema = json.load(f)
        
    with open(targets_path, "r") as f:
        targets = json.load(f)
        
    try:
        jsonschema.validate(instance=targets, schema=schema)
        print("Validation successful: targets match schema.")
    except jsonschema.exceptions.ValidationError as err:
        print("Validation error:", err)
        sys.exit(1)
        
    # Additional validation: check for uniqueness
    ids = [t["id"] for t in targets["targets"]]
    if len(ids) != len(set(ids)):
        print("Validation error: Duplicate target IDs found.")
        sys.exit(1)

    app_names = [t["app_service_name"] for t in targets["targets"]]
    if len(app_names) != len(set(app_names)):
        print("Validation error: Duplicate app service names found.")
        sys.exit(1)

if __name__ == "__main__":
    main()
