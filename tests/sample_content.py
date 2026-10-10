"""Host content used by the kit's tests: two purposes and a short notice in both languages."""

PURPOSES = [
    {
        "code": "service",
        "category": "UTILITY",
        "position": 1,
        "labels": {
            "en": {"name": "Service messages", "description": "appointment reminders, payment and renewal notices"},
            "hi": {"name": "सेवा संदेश", "description": "अपॉइंटमेंट रिमाइंडर, भुगतान और नवीनीकरण की सूचनाएँ"},
        },
    },
    {
        "code": "updates",
        "category": "MARKETING",
        "position": 2,
        "labels": {
            "en": {"name": "Updates and offers", "description": "health-day motivation, greetings, tips and offers"},
            "hi": {"name": "अपडेट और ऑफ़र", "description": "स्वास्थ्य दिवस पर प्रेरणा, शुभकामनाएँ, टिप्स और ऑफ़र"},
        },
    },
]

NOTICES = [
    {
        "version": 1,
        "language": "en",
        "effective_from": "2026-10-11",
        "title": "WhatsApp messages: privacy notice",
        "summary": "We keep only your name, WhatsApp number and, if you give it, your birthday.",
        "body": "Who is responsible. The business named on this page.\n\nWhat we collect. Name, number, birthday.\n\n"
        "Your rights. Withdraw or delete at any time from your preferences page.",
    },
    {
        "version": 1,
        "language": "hi",
        "effective_from": "2026-10-11",
        "title": "WhatsApp संदेश: गोपनीयता सूचना",
        "summary": "हम केवल आपका नाम, WhatsApp नंबर और, अगर आप दें तो, आपका जन्मदिन रखते हैं।",
        "body": "ज़िम्मेदार कौन है। इस पेज पर बताया गया व्यवसाय।\n\nहम क्या लेते हैं। नाम, नंबर, जन्मदिन।\n\n"
        "आपके अधिकार। अपनी पसंद वाले पेज से कभी भी सहमति वापस लें या डेटा हटाएँ।",
    },
]


def load_sample_content(conn) -> None:
    from whatsapp_consent.library import load_notices, load_purposes

    load_purposes(conn, PURPOSES)
    load_notices(conn, NOTICES)
