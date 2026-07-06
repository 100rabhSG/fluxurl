package com.fluxurl.service;

import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.assertThat;

public class ShortnerServiceTest {

    private final ShortnerService shortnerService = new ShortnerService();

    @Test
    public void testGenerateShortCode_LengthAndAlphabet() {
        String code = shortnerService.generateShortCode();

        // Assert length
        assertThat(code).hasSize(7);

        // Assert characters match base62
        assertThat(code).matches("^[a-zA-Z0-9]{7}$");
    }

    @Test
    public void testGenerateShortCode_Uniqueness() {
        String code1 = shortnerService.generateShortCode();
        String code2 = shortnerService.generateShortCode();

        assertThat(code1).isNotEqualTo(code2);
    }
}