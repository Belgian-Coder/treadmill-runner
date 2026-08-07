import Toybox.Lang;

module GatewaySettings {
    function isConfigured(baseUrl, token) as Boolean {
        return baseUrl instanceof String &&
            token instanceof String &&
            (baseUrl as String).find("https://") == 0 &&
            (token as String).length() >= 20;
    }
}
