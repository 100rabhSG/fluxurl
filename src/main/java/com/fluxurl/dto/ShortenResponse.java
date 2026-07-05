package com.fluxurl.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ShortenResponse {
    private String shortCode;
    private String shortUrl;
    private String longUrl;
}
