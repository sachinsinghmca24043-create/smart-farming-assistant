
import json
from collections import Counter
from pathlib import Path

from services.intent_service import analyze_intent

# 60 manually labelled queries: 20 per language.
# Expected labels use the exact keys in INTENT_LABELS.
CASES = [
    # English — crop care
    ("How do I take care of my rice crop?", "crop_care", "en"),
    ("What is the best way to grow wheat?", "crop_care", "en"),
    ("How can I improve tomato growth?", "crop_care", "en"),
    ("Tell me how to cultivate maize.", "crop_care", "en"),
    ("How should I look after cotton plants?", "crop_care", "en"),

    # English — pests and diseases
    ("How do I control pests in rice?", "pest_disease", "en"),
    ("Why are insects eating my tomato leaves?", "pest_disease", "en"),
    ("My wheat plants have a disease. What should I do?", "pest_disease", "en"),
    ("How can I prevent fungal infection in crops?", "pest_disease", "en"),
    ("What should I do about aphids?", "pest_disease", "en"),

    # English — fertilizer
    ("Which fertilizer is suitable for rice?", "fertilizer", "en"),
    ("How can I improve soil nutrients?", "fertilizer", "en"),
    ("When should I apply manure to wheat?", "fertilizer", "en"),
    ("What nutrients do tomato plants need?", "fertilizer", "en"),
    ("How much fertilizer should I use?", "fertilizer", "en"),

    # English — irrigation
    ("When should I water my rice crop?", "irrigation", "en"),
    ("How often should wheat be irrigated?", "irrigation", "en"),
    ("How much water does a tomato plant need?", "irrigation", "en"),
    ("When should I stop watering before harvest?", "irrigation", "en"),
    ("How can I manage irrigation on my farm?", "irrigation", "en"),

    # English — weather
    ("Will it rain tomorrow?", "weather", "en"),
    ("What is the weather forecast for my farm?", "weather", "en"),
    ("Should I protect crops from heavy rain?", "weather", "en"),
    ("Is the weather suitable for sowing?", "weather", "en"),
    ("Will strong winds damage my crops?", "weather", "en"),

    # English — government schemes
    ("What government subsidies are available for farmers?", "government_schemes", "en"),
    ("How can I apply for a farming scheme?", "government_schemes", "en"),
    ("Is there a government loan scheme for farmers?", "government_schemes", "en"),
    ("Where can I find agricultural support programs?", "government_schemes", "en"),
    ("How do I check my farmer scheme eligibility?", "government_schemes", "en"),

    # Hindi — crop care
    ("धान की फसल की देखभाल कैसे करें?", "crop_care", "hi"),
    ("गेहूं की खेती कैसे करें?", "crop_care", "hi"),
    ("टमाटर के पौधों की वृद्धि कैसे बढ़ाएं?", "crop_care", "hi"),
("मक्का की फसल कैसे उगाएं?", "crop_care", "hi"),
("कपास की फसल की देखभाल कैसे करें?", "crop_care", "hi"),

    # Hindi — pests and diseases
    ("धान में कीटों को कैसे नियंत्रित करें?", "pest_disease", "hi"),
    ("टमाटर के पत्तों में कीड़े लग गए हैं, क्या करूं?", "pest_disease", "hi"),
    ("गेहूं की फसल में बीमारी लग गई है।", "pest_disease", "hi"),
    ("फसल में फफूंद से कैसे बचाएं?", "pest_disease", "hi"),
    ("पौधों पर माहू कीट का नियंत्रण कैसे करें?", "pest_disease", "hi"),

    # Hindi — fertilizer
    ("धान में कौन सी खाद डालनी चाहिए?", "fertilizer", "hi"),
    ("मिट्टी में पोषक तत्व कैसे बढ़ाएं?", "fertilizer", "hi"),
    ("गेहूं में गोबर की खाद कब डालें?", "fertilizer", "hi"),
    ("टमाटर को कौन से पोषक तत्व चाहिए?", "fertilizer", "hi"),
    ("फसल में कितना उर्वरक डालना चाहिए?", "fertilizer", "hi"),

    # Hindi — irrigation
    ("धान में पानी कब देना चाहिए?", "irrigation", "hi"),
    ("गेहूं की सिंचाई कितनी बार करनी चाहिए?", "irrigation", "hi"),
    ("टमाटर के पौधे को कितना पानी चाहिए?", "irrigation", "hi"),
    ("कटाई से पहले पानी कब बंद करें?", "irrigation", "hi"),
    ("खेत में सिंचाई का प्रबंधन कैसे करें?", "irrigation", "hi"),

    # Hindi — weather
    ("क्या कल बारिश होगी?", "weather", "hi"),
    ("मेरे खेत के लिए मौसम का पूर्वानुमान क्या है?", "weather", "hi"),
    ("तेज बारिश से फसल को कैसे बचाएं?", "weather", "hi"),
    ("बुवाई के लिए मौसम कैसा होना चाहिए?", "weather", "hi"),
    ("तेज हवा से फसल को नुकसान होगा क्या?", "weather", "hi"),

    # Kannada — crop care
    ("ನನ್ನ ಭತ್ತದ ಬೆಳೆಯನ್ನು ಹೇಗೆ ನೋಡಿಕೊಳ್ಳಬೇಕು?", "crop_care", "kn"),
    ("ಗೋಧಿಯನ್ನು ಹೇಗೆ ಬೆಳೆಸಬೇಕು?", "crop_care", "kn"),
    ("ಟೊಮೇಟೊ ಗಿಡದ ಬೆಳವಣಿಗೆಯನ್ನು ಹೇಗೆ ಹೆಚ್ಚಿಸಬೇಕು?", "crop_care", "kn"),
    ("ಮೆಕ್ಕೆಜೋಳವನ್ನು ಹೇಗೆ ಬೆಳೆಸಬೇಕು?", "crop_care", "kn"),
    ("ಹತ್ತಿ ಬೆಳೆಯನ್ನು ಹೇಗೆ ನೋಡಿಕೊಳ್ಳಬೇಕು?", "crop_care", "kn"),

    # Kannada — pests and diseases
    ("ಭತ್ತದ ಬೆಳೆಯಲ್ಲಿ ಕೀಟಗಳನ್ನು ಹೇಗೆ ನಿಯಂತ್ರಿಸಬೇಕು?", "pest_disease", "kn"),
    ("ಟೊಮೇಟೊ ಎಲೆಗಳನ್ನು ಕೀಟಗಳು ತಿನ್ನುತ್ತಿವೆ, ಏನು ಮಾಡಬೇಕು?", "pest_disease", "kn"),
    ("ಗೋಧಿ ಬೆಳೆಗೆ ರೋಗ ಬಂದಿದೆ, ಏನು ಮಾಡಬೇಕು?", "pest_disease", "kn"),
    ("ಬೆಳೆಗಳಲ್ಲಿ ಶಿಲೀಂಧ್ರ ರೋಗವನ್ನು ಹೇಗೆ ತಡೆಯಬೇಕು?", "pest_disease", "kn"),
    ("ಗಿಡಗಳ ಮೇಲಿನ ಕೀಟಗಳನ್ನು ಹೇಗೆ ನಿಯಂತ್ರಿಸಬೇಕು?", "pest_disease", "kn"),
]


def main():
    total = len(CASES)
    correct = 0
    by_language = Counter()
    language_totals = Counter()
    by_intent = Counter()
    intent_totals = Counter()
    errors = []
    details = []

    for text, expected, language in CASES:
        result = analyze_intent(text, language)
        predicted = result.get("intent", "other")
        matched = predicted == expected

        correct += matched
        by_language[language] += matched
        language_totals[language] += 1
        by_intent[expected] += matched
        intent_totals[expected] += 1

        row = {
            "language": language,
            "text": text,
            "expected": expected,
            "predicted": predicted,
            "correct": bool(matched),
            "confidence": result.get("confidence"),
            "fallback_used": result.get("fallback_used"),
            "entities": result.get("entities", {}),
        }
        details.append(row)

        if not matched:
            errors.append(row)

    report = {
        "total_queries": total,
        "correct": correct,
        "incorrect": total - correct,
        "accuracy_percent": round(correct / total * 100, 2),
        "by_language": {
            language: {
                "correct": by_language[language],
                "total": language_totals[language],
                "accuracy_percent": round(
                    by_language[language] / language_totals[language] * 100, 2
                ),
            }
            for language in sorted(language_totals)
        },
        "by_expected_intent": {
            intent: {
                "correct": by_intent[intent],
                "total": intent_totals[intent],
                "accuracy_percent": round(
                    by_intent[intent] / intent_totals[intent] * 100, 2
                ),
            }
            for intent in sorted(intent_totals)
        },
        "misclassified_queries": errors,
        "details": details,
        "note": (
            "Manual test set; results measure this sample only, "
            "not general real-world accuracy."
        ),
    }

    output_path = Path("intent_evaluation_report.json")
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Total queries: {total}")
    print(f"Correct: {correct}")
    print(f"Incorrect: {total - correct}")
    print(f"Accuracy: {report['accuracy_percent']}%")

    print("\nAccuracy by language:")
    for language, stats in report["by_language"].items():
        print(
            f"  {language}: {stats['correct']}/{stats['total']} "
            f"({stats['accuracy_percent']}%)"
        )

    print("\nAccuracy by expected intent:")
    for intent, stats in report["by_expected_intent"].items():
        print(
            f"  {intent}: {stats['correct']}/{stats['total']} "
            f"({stats['accuracy_percent']}%)"
        )

    print(f"\nReport saved to: {output_path.resolve()}")

    if errors:
        print("\nMisclassified queries:")
        for item in errors:
            print(
                f"- [{item['language']}] {item['text']}\n"
                f"  Expected: {item['expected']}; "
                f"Predicted: {item['predicted']}"
            )


if __name__ == "__main__":
    main()
