import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
const styles = cva('button', { variants: { variant: { default: 'primary', secondary: 'secondary', ghost: 'ghost', destructive: 'destructive' }, size: { default: '', sm: 'small', icon: 'icon-button' } }, defaultVariants: { variant: 'default', size: 'default' } });
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof styles> { asChild?: boolean }
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild, ...props }, ref) => { const Component = asChild ? Slot : 'button'; return <Component ref={ref} className={twMerge(clsx(styles({ variant, size }), className))} {...props} />; });
Button.displayName = 'Button';
