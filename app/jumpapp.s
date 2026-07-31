    .syntax unified
    .cpu cortex-m4
    .thumb
    .text
    .align 2

    .global JumpApp
    .type JumpApp, %function

JumpApp:
    LDR     SP, [R0, #0]
    LDR     PC, [R0, #4]
    .size JumpApp, .-JumpApp
