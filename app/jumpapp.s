    .syntax unified
    .cpu cortex-m4
    .thumb
    .text
    .align 2

    .global JumpApp
    .type JumpApp, %function

JumpApp:
    MOV     R3, R0
    LDR     R1, [R3, #0]
    LDR     R2, [R3, #4]

    /* Restore thread mode to the reset defaults before entering the app. */
    MOVS    R0, #0
    MSR     CONTROL, R0
    MSR     BASEPRI, R0
    MSR     FAULTMASK, R0
    ISB
    MSR     MSP, R1
    CPSIE   I
    BX      R2
    .size JumpApp, .-JumpApp
