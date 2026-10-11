"""Every visible string on the kit's pages, in every Language. Tests check completeness.

Purpose names and notice texts are not here: they are host content (whatsapp_consent.library).
Adding a Language means adding its text to every entry below and to MONTHS.
"""

from whatsapp_consent.enums import Language

MONTHS: dict[Language, list[str]] = {
    Language.EN: [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    Language.HI: ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
}

STRINGS: dict[str, dict[str, str]] = {
    "lang_name": {"en": "English", "hi": "हिंदी"},
    "footer": {
        "en": "Your choices are recorded securely. You can change them at any time.",
        "hi": "आपकी पसंद सुरक्षित रूप से दर्ज की जाती है। आप इसे कभी भी बदल सकते हैं।",
    },
    "page_title_consent": {"en": "Stay in touch on WhatsApp", "hi": "WhatsApp पर जुड़े रहें"},
    "not_found_title": {"en": "Page not found", "hi": "पेज नहीं मिला"},
    "not_found_body": {"en": "Please check the link.", "hi": "कृपया लिंक जाँच लें।"},
    "link_invalid_title": {"en": "This link isn't valid any more", "hi": "यह लिंक अब मान्य नहीं है"},
    "link_invalid_body": {
        "en": "Please ask the business that sent it for a new link.",
        "hi": "कृपया जिस व्यवसाय ने यह भेजा है, उससे नया लिंक माँगें।",
    },
    "already_done_title": {"en": "You've already confirmed", "hi": "आप पहले ही पुष्टि कर चुके हैं"},
    "already_done_body": {
        "en": "To change what you receive, open your preferences.",
        "hi": "आपको क्या मिले, यह बदलने के लिए अपनी पसंद खोलें।",
    },
    "open_preferences": {"en": "Open my preferences", "hi": "मेरी पसंद खोलें"},
    "session_expired": {
        "en": "Your session has expired. Please open the link again.",
        "hi": "आपका सत्र समाप्त हो गया है। कृपया लिंक फिर से खोलें।",
    },
    # Consent form
    "intro": {
        "en": "{business} would like to send you messages on WhatsApp. Choose what you're happy to receive.",
        "hi": "{business} आपको WhatsApp पर संदेश भेजना चाहते हैं। चुनें कि आप कौन-से संदेश पाना चाहते हैं।",
    },
    "who_label": {"en": "Who is this for?", "hi": "यह किसके लिए है?"},
    "who_me": {"en": "Me", "hi": "मेरे लिए"},
    "who_child": {"en": "My child / someone in my care", "hi": "मेरे बच्चे / मेरी देखरेख में किसी के लिए"},
    "change_who": {"en": "Not for you? Choose again", "hi": "किसी और के लिए? फिर से चुनें"},
    "name": {"en": "Your name", "hi": "आपका नाम"},
    "phone": {"en": "Your WhatsApp number", "hi": "आपका WhatsApp नंबर"},
    "birth_year": {"en": "Year of birth", "hi": "जन्म का वर्ष"},
    "birth_year_hint": {
        "en": "Used only to check your age. We don't save it.",
        "hi": "केवल आपकी उम्र जाँचने के लिए। हम इसे सेव नहीं करते।",
    },
    "birthday": {"en": "Birthday (optional)", "hi": "जन्मदिन (वैकल्पिक)"},
    "child_birthday": {"en": "Child's birthday (optional)", "hi": "बच्चे का जन्मदिन (वैकल्पिक)"},
    "birthday_hint": {
        "en": "Day and month only, for birthday wishes.",
        "hi": "केवल दिन और महीना, जन्मदिन की शुभकामनाओं के लिए।",
    },
    "day": {"en": "Day", "hi": "दिन"},
    "month": {"en": "Month", "hi": "महीना"},
    "guardian_name": {"en": "Your name (parent or guardian)", "hi": "आपका नाम (माता-पिता या अभिभावक)"},
    "guardian_relation": {"en": "Your relationship to the child", "hi": "बच्चे से आपका संबंध"},
    "guardian_relation_placeholder": {"en": "e.g. Mother, Father, Guardian", "hi": "जैसे माँ, पिता, अभिभावक"},
    "guardian_phone": {"en": "Your WhatsApp number", "hi": "आपका WhatsApp नंबर"},
    "child_name": {"en": "Child's first name", "hi": "बच्चे का पहला नाम"},
    "declaration": {
        "en": "I am 18 or older and this child's parent or legal guardian.",
        "hi": "मेरी उम्र 18 वर्ष या उससे अधिक है और मैं इस बच्चे का/की माता-पिता या कानूनी अभिभावक हूँ।",
    },
    "purposes_label": {"en": "What would you like to receive?", "hi": "आप क्या पाना चाहते हैं?"},
    "no_health_info": {
        "en": "Please don't share health details here. This form doesn't ask for them.",
        "hi": "कृपया यहाँ स्वास्थ्य से जुड़ी जानकारी न दें। इस फ़ॉर्म में इसकी ज़रूरत नहीं है।",
    },
    "read_notice": {"en": "Read the full notice", "hi": "पूरी सूचना पढ़ें"},
    "submit": {"en": "Continue", "hi": "आगे बढ़ें"},
    # Errors
    "err_name": {"en": "Please enter your name.", "hi": "कृपया अपना नाम लिखें।"},
    "err_phone": {"en": "Please enter a valid WhatsApp number.", "hi": "कृपया सही WhatsApp नंबर लिखें।"},
    "err_birth_year": {"en": "Please enter your year of birth.", "hi": "कृपया अपने जन्म का वर्ष लिखें।"},
    "err_birthday": {
        "en": "Please choose a valid birthday, or leave both empty.",
        "hi": "कृपया सही जन्मदिन चुनें, या दोनों खाली छोड़ दें।",
    },
    "err_purposes": {"en": "Choose at least one type of message.", "hi": "कृपया कम से कम एक प्रकार का संदेश चुनें।"},
    "err_guardian": {
        "en": "Please enter your name and your relationship to the child.",
        "hi": "कृपया अपना नाम और बच्चे से अपना संबंध लिखें।",
    },
    "err_child_name": {"en": "Please enter the child's first name.", "hi": "कृपया बच्चे का पहला नाम लिखें।"},
    "err_declaration": {
        "en": "Please confirm you are the child's parent or guardian.",
        "hi": "कृपया पुष्टि करें कि आप बच्चे के माता-पिता या अभिभावक हैं।",
    },
    "err_under_18": {
        "en": "Because you're under 18, a parent or guardian needs to fill this in.",
        "hi": "आपकी उम्र 18 वर्ष से कम है, इसलिए इसे माता-पिता या अभिभावक को भरना होगा।",
    },
    "err_minor_needs_guardian": {
        "en": "This person is recorded as under 18. Please fill this in as their parent or guardian.",
        "hi": "इस व्यक्ति की उम्र 18 वर्ष से कम दर्ज है। कृपया माता-पिता या अभिभावक के रूप में भरें।",
    },
    "err_duplicate": {
        "en": "Someone with this name and number is already registered with this business.",
        "hi": "इस नाम और नंबर वाला व्यक्ति पहले से इस व्यवसाय के साथ पंजीकृत है।",
    },
    "err_rate_limited": {
        "en": "Too many attempts. Please try again later.",
        "hi": "बहुत अधिक प्रयास हुए। कृपया बाद में फिर से कोशिश करें।",
    },
    "err_delivery_failed": {
        "en": "We couldn't reach this number on WhatsApp. Please check it.",
        "hi": "हम इस नंबर पर WhatsApp से संपर्क नहीं कर सके। कृपया नंबर जाँच लें।",
    },
    "err_too_soon": {
        "en": "Please wait a minute before asking for a new code.",
        "hi": "नया कोड माँगने से पहले कृपया एक मिनट रुकें।",
    },
    "err_wrong_code": {
        "en": "That code isn't right. Please try again.",
        "hi": "यह कोड सही नहीं है। कृपया फिर से कोशिश करें।",
    },
    "err_expired": {
        "en": "This code has expired. Send a new one.",
        "hi": "इस कोड की समय-सीमा समाप्त हो गई है। नया कोड भेजें।",
    },
    "err_too_many_attempts": {
        "en": "Too many wrong codes. Send a new one.",
        "hi": "बहुत सारे गलत कोड डाले गए। नया कोड भेजें।",
    },
    "err_not_found": {
        "en": "This code can't be used any more. Send a new one.",
        "hi": "यह कोड अब इस्तेमाल नहीं किया जा सकता। नया कोड भेजें।",
    },
    "err_not_received_yet": {
        "en": "We haven't received your WhatsApp message yet. Send it, then tap the button again.",
        "hi": "हमें अभी तक आपका WhatsApp संदेश नहीं मिला है। संदेश भेजें, फिर बटन दोबारा दबाएँ।",
    },
    # Code page
    "verify_title": {"en": "Enter the code", "hi": "कोड डालें"},
    "verify_intro": {
        "en": "We sent a 6-digit code on WhatsApp to {phone}.",
        "hi": "हमने {phone} पर WhatsApp से 6 अंकों का कोड भेजा है।",
    },
    "code": {"en": "Code", "hi": "कोड"},
    "verify_submit": {"en": "Verify", "hi": "पुष्टि करें"},
    "resend": {"en": "Send a new code", "hi": "नया कोड भेजें"},
    "code_sent": {"en": "A new code is on its way.", "hi": "नया कोड भेजा जा रहा है।"},
    "click_title": {"en": "Or confirm on WhatsApp", "hi": "या WhatsApp पर पुष्टि करें"},
    "click_intro": {
        "en": "Open WhatsApp with a message ready for {business}. Send it, then come back here.",
        "hi": "WhatsApp खोलें, {business} के लिए संदेश तैयार मिलेगा। उसे भेजें, फिर यहाँ लौटें।",
    },
    "click_button": {"en": "Continue on WhatsApp", "hi": "WhatsApp पर आगे बढ़ें"},
    "click_check": {"en": "I've sent it", "hi": "मैंने भेज दिया"},
    # Done
    "done_title": {"en": "You're all set", "hi": "सब तैयार है"},
    "done_intro": {
        "en": "{business} can now send you the messages you chose.",
        "hi": "{business} अब आपको आपके चुने हुए संदेश भेज सकते हैं।",
    },
    "save_number": {
        "en": "Save {business}'s WhatsApp number so their messages reach you.",
        "hi": "{business} का WhatsApp नंबर सेव कर लें ताकि उनके संदेश आप तक पहुँचें।",
    },
    "manage_link": {"en": "Manage your preferences or delete your data", "hi": "अपनी पसंद बदलें या अपना डेटा हटाएँ"},
    "manage_hint": {
        "en": "Bookmark this private link. You can also open it later by entering your number.",
        "hi": "इस निजी लिंक को सेव कर लें। आप बाद में अपना नंबर डालकर भी इसे खोल सकते हैं।",
    },
    # Preferences
    "prefs_title": {"en": "Your WhatsApp preferences", "hi": "आपकी WhatsApp पसंद"},
    "prefs_intro": {
        "en": "Choose what each business can send you. Changes apply immediately.",
        "hi": "चुनें कि हर व्यवसाय आपको क्या भेज सकता है। बदलाव तुरंत लागू होते हैं।",
    },
    "prefs_link_invalid_body": {
        "en": "You can open your preferences again by entering your number.",
        "hi": "आप अपना नंबर डालकर अपनी पसंद फिर से खोल सकते हैं।",
    },
    "for_child": {"en": "For {name}", "hi": "{name} के लिए"},
    "save": {"en": "Save", "hi": "सेव करें"},
    "saved": {"en": "Saved.", "hi": "सेव हो गया।"},
    "no_subjects": {"en": "There's nothing to manage for this number.", "hi": "इस नंबर के लिए बदलने को कुछ नहीं है।"},
    "delete_data": {"en": "Delete my data", "hi": "मेरा डेटा हटाएँ"},
    "delete_all": {"en": "Delete my data from all businesses", "hi": "सभी व्यवसायों से मेरा डेटा हटाएँ"},
    "delete_confirm_title": {"en": "Confirm deletion", "hi": "हटाने की पुष्टि करें"},
    "delete_confirm_intro": {
        "en": "We sent a code on WhatsApp to {phone}. Enter it to delete your data. This can't be undone.",
        "hi": "हमने {phone} पर WhatsApp से कोड भेजा है। अपना डेटा हटाने के लिए इसे डालें। इसे वापस नहीं किया जा सकता।",
    },
    "delete_submit": {"en": "Delete permanently", "hi": "हमेशा के लिए हटाएँ"},
    "deleted_title": {"en": "Your data has been deleted", "hi": "आपका डेटा हटा दिया गया है"},
    "deleted_body": {
        "en": "You won't receive any more messages from this business through us. A record that you gave and then "
        "withdrew consent is kept without your name or number for {days} days, then removed.",
        "hi": "अब आपको हमारे ज़रिए इस व्यवसाय से कोई संदेश नहीं मिलेगा। आपने सहमति दी और फिर वापस ली, इसका रिकॉर्ड "
        "आपके नाम या नंबर के बिना {days} दिनों तक रखा जाता है, फिर हटा दिया जाता है।",
    },
    "lookup_title": {"en": "Manage your preferences", "hi": "अपनी पसंद बदलें"},
    "lookup_intro": {
        "en": "Enter your WhatsApp number. We'll send a code to confirm it's you.",
        "hi": "अपना WhatsApp नंबर डालें। यह पुष्टि करने के लिए कि यह आप ही हैं, हम एक कोड भेजेंगे।",
    },
    "lookup_submit": {"en": "Send code", "hi": "कोड भेजें"},
    "lookup_sent": {
        "en": "If this number is registered, we've sent a code on WhatsApp.",
        "hi": "अगर यह नंबर पंजीकृत है, तो हमने WhatsApp पर एक कोड भेजा है।",
    },
    # Notice
    "notice_version": {
        "en": "Notice version {version}, in effect from {date}",
        "hi": "सूचना संस्करण {version}, {date} से लागू",
    },
}


def t(key: str, lang: Language, **fmt: object) -> str:
    text = STRINGS[key][lang.value]
    return text.format(**fmt) if fmt else text


def pick_lang(value: str | None, fallback: Language) -> Language:
    try:
        return Language(value) if value else fallback
    except ValueError:
        return fallback


def other_lang(lang: Language) -> Language:
    """The language the switch link offers (with two languages, the other one)."""
    languages = list(Language)
    return languages[(languages.index(lang) + 1) % len(languages)]
