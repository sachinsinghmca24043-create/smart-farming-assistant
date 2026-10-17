
from gradio_client import Client

spaces = [
    "D-Jaden02/Krutrim_English_Hi_Translation",
    "D-Jaden02/krutrim-south-languages",
]

for space in spaces:
    print(f"\n--- Checking {space} ---")
    try:
        client = Client(space)
        client.view_api()
    except Exception as exc:
        print(f"Could not inspect Space: {type(exc).__name__}: {exc}")
