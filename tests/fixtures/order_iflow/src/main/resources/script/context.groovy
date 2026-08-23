def Message processData(Message message) {
    def correlationId = message.getHeader("X-Correlation-ID")
    message.setProperty("ProcessedBy", "fixture")
    return message
}

