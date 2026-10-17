from services.intent_service import analyze_intent

CASES = [
    ("en", "My paddy field has standing water after heavy rain.", "waterlogging"),
    ("en", "Can you help me understand crop rotation?", "crop_rotation"),
    ("en", "Where can an Indian farmer get agricultural advice?", "expert_support"),
    ("en", "What is the capital of France?", "other"),
    ("en", "Explain the difference between a list and a tuple.", "other"),
    ("en", "My tomato leaves have yellow spots.", "pest_disease"),
    ("en", "How can I conserve water while growing rice?", "irrigation"),
    ("en", "Where can I check eligibility for a farming subsidy?", "government_schemes"),
    ("hi", "मेरे खेत में बारिश के बाद बहुत पानी भर गया है।", "waterlogging"),
    ("hi", "फसल चक्र अपनाने का क्या फायदा है?", "crop_rotation"),
    ("hi", "किसान कृषि विशेषज्ञ से सलाह कैसे ले सकते हैं?", "expert_support"),
    ("hi", "भारत की राजधानी क्या है?", "other"),
    ("hi", "पौधों की पत्तियों पर पीले धब्बे हैं।", "pest_disease"),
    ("hi", "धान की सिंचाई कैसे करनी चाहिए?", "irrigation"),
    ("kn", "ಮಳೆಯ ನಂತರ ನನ್ನ ಹೊಲದಲ್ಲಿ ನೀರು ನಿಂತಿದೆ.", "waterlogging"),
    ("kn", "ಬೆಳೆ ಪರಿವರ್ತನೆಯ ಪ್ರಯೋಜನವೇನು?", "crop_rotation"),
    ("kn", "ರೈತರು ಕೃಷಿ ತಜ್ಞರ ಸಲಹೆಯನ್ನು ಹೇಗೆ ಪಡೆಯಬಹುದು?", "expert_support"),
    ("kn", "ಭಾರತದ ರಾಜಧಾನಿ ಯಾವುದು?", "other"),
    ("kn", "ಟೊಮೇಟೊ ಎಲೆಗಳಲ್ಲಿ ಹಳದಿ ಕಲೆಗಳಿವೆ.", "pest_disease"),
    ("kn", "ಭತ್ತಕ್ಕೆ ನೀರು ಯಾವಾಗ ಕೊಡಬೇಕು?", "irrigation"),
]

correct = 0
print("UNSEEN INTENT VALIDATION")
print("-" * 72)

for language, message, expected in CASES:
    result = analyze_intent(message, language)
    predicted = result["intent"]
    passed = predicted == expected
    correct += passed

    print(f"[{'PASS' if passed else 'FAIL'}] {language}")
    print(f"  Message:  {message}")
    print(f"  Expected: {expected}")
    print(f"  Predicted: {predicted}")
    print(f"  Fallback: {result.get('fallback_used')}")
    print()

total = len(CASES)
print(f"Result: {correct}/{total} correct ({correct / total * 100:.2f}%)")
print("This is a small diagnostic set, not a general accuracy estimate.")
