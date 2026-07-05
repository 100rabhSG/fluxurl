package com.fluxurl.service;

import org.springframework.stereotype.Service;

import java.security.SecureRandom;

@Service
public class ShortnerService {
    public static final String ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
    public static final int DEFAULT_LENGTH = 7;

    // SecureRandom is thread-safe and cryptographically secure
    private final SecureRandom random = new SecureRandom();

    //Generate a random 7-char Base62 string
    public String generateShortCode(){
        return generateShortCode(DEFAULT_LENGTH)
    }

    public String generateShortCode(int length){
        StringBuilder code = new StringBuilder(length);

        for(int i=0; i<length; i++){
            int randomIndex = random.nextInt(ALPHABET.length());
            code.append(ALPHABET.charAt(randomIndex));
        }

        return code.toString();
    }
}
