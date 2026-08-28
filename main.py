import json
from src.extract import extract_structured_data

def main():
    
    try:
        
        result = extract_structured_data("/Users/shubhamgore/Development/Warp_TakeHome/mock/warp-proposal-challenge/calls/call_01_northwind.txt")

        print(json.dumps(result.model_dump(), indent=4))
    except Exception as e:
        print("An error occurred : ", e)

if __name__ == "__main__":
    main()
